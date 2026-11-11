from datetime import date

import pytest
from fastapi.testclient import TestClient

from fintrack import analytics, db
from fintrack.importers import ImportResult, Txn
from fintrack.web.app import create_app


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "w.db")
    yield c
    c.close()


def test_default_groups_and_override(conn):
    txns = [
        Txn(account="Revolut", date=date(2026, 10, 1), description="Continente", amount=-20),
        Txn(account="Revolut Poupança CC", date=date(2026, 10, 1), description="Juros", amount=0.5, kind="INTEREST"),
    ]
    db.save_import(conn, ImportResult(source="revolut", transactions=txns), "x")
    g = analytics.groups_for_accounts(conn)
    assert g == {"Revolut": "Pessoal", "Revolut Poupança CC": "Poupanças"}
    conn.execute("INSERT INTO account_groups VALUES ('Revolut', 'Conjunta')")
    assert analytics.groups_for_accounts(conn)["Revolut"] == "Conjunta"


def test_valuations_track_change(conn):
    rows = [("PPR ActivoBank", "Poupanças", "2026-08-01", 1000.0), ("PPR ActivoBank", "Poupanças", "2026-10-01", 1050.0),
            ("Bitcoin", "Cripto", "2026-10-01", 300.0)]
    conn.executemany("INSERT INTO valuations (asset, grupo, date, value) VALUES (?, ?, ?, ?)", rows)
    summary = {a["asset"]: a for a in analytics.valuation_summary(conn)}
    assert summary["PPR ActivoBank"]["last"] == 1050.0
    assert summary["PPR ActivoBank"]["change"] == 50.0
    assert summary["PPR ActivoBank"]["change_pct"] == 5.0


def test_btc_invested_from_exchange_rows(conn):
    txns = [
        Txn(account="Revolut", date=date(2026, 10, 1), description="Conversão cambial para BTC", amount=-0.20, kind="EXCHANGE"),
        Txn(account="Revolut", date=date(2026, 10, 2), description="Conversão cambial para BTC", amount=-0.80, kind="EXCHANGE"),
        Txn(account="Revolut", date=date(2026, 10, 3), description="Continente", amount=-5),
    ]
    db.save_import(conn, ImportResult(source="revolut", transactions=txns), "x")
    assert analytics.btc_invested(conn) == (1.0, 2)


def test_wealth_page_and_group_change(tmp_path):
    app = create_app(tmp_path / "p.db", require_login=False)
    client = TestClient(app)
    db.save_import(app.state.conn, ImportResult(source="revolut", transactions=[
        Txn(account="Revolut Poupança CC", date=date(2026, 10, 1), description="Juros", amount=1, kind="INTEREST")]), "x")
    assert client.get("/patrimonio?tab=Poupanças").status_code == 200
    r = client.post("/patrimonio/grupo", data={"account": "Revolut Poupança CC", "grupo": "Conjunta"}, follow_redirects=False)
    assert r.status_code == 303 and "Conjunta" in r.headers["location"]
    r = client.post("/patrimonio/valor", data={"asset": "PPR ActivoBank", "grupo": "Poupanças", "value": "1.025,50",
                                              "day": "2026-11-01", "note": "mensal"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/patrimonio?tab=Poupanças").text
    assert "PPR ActivoBank" in page and "1.025,50" in page
    assert client.get("/patrimonio?tab=Cripto").status_code == 200


def test_btc_manual_cost_overrides_statements(conn):
    txns = [Txn(account="Revolut", date=date(2026, 10, 1), description="Conversão cambial para BTC", amount=-0.20, kind="EXCHANGE")]
    db.save_import(conn, ImportResult(source="revolut", transactions=txns), "x")
    assert analytics.btc_cost(conn)["value"] == 0.2
    conn.execute("INSERT INTO manual_costs (key, value) VALUES ('bitcoin', 137.23)")
    assert analytics.btc_cost(conn) == {"value": 137.23, "source": "manual"}


def test_bitcoin_gain_is_against_cost(tmp_path):
    app = create_app(tmp_path / "b.db", require_login=False)
    client = TestClient(app)
    conn = app.state.conn
    conn.execute("INSERT INTO valuations (asset, grupo, date, value) VALUES ('Bitcoin', 'Cripto', '2026-10-01', 140)")
    conn.execute("INSERT INTO valuations (asset, grupo, date, value) VALUES ('Bitcoin', 'Cripto', '2026-11-11', 147.27)")
    conn.execute("INSERT INTO manual_costs (key, value) VALUES ('bitcoin', 137.23)")
    conn.commit()
    page = client.get("/patrimonio?tab=Cripto").text
    assert "10,04" in page and "7.32%" in page


def test_planning_pages_and_forms(tmp_path):
    app = create_app(tmp_path / "pl.db", require_login=False)
    c = TestClient(app)
    assert c.get("/planear").status_code == 200
    assert c.post("/planear/orcamento", data={"category": "Restauração", "monthly": "150"}, follow_redirects=False).status_code == 303
    assert c.post("/planear/meta", data={"name": "Fundo", "target": "5000", "saved": "1.000,00", "target_date": "2027-12-01"}, follow_redirects=False).status_code == 303
    page = c.get("/planear").text
    assert "Fundo" in page and "150" in page
    assert c.get("/conjunta").status_code == 200
    assert c.post("/conjunta/1", data={"percent": "50"}, follow_redirects=False).status_code == 303


def test_shared_owed_and_forecast_math(conn):
    txns = [Txn(account="Revolut", date=date(2026, 11, 2), description="Continente", amount=-80)]
    db.save_import(conn, ImportResult(source="revolut", transactions=txns), "x")
    tid = conn.execute("SELECT id FROM transactions").fetchone()[0]
    conn.execute("INSERT INTO shared_splits VALUES (?, 50)", (tid,))
    assert analytics.shared_summary(conn, date(2026, 11, 10))["owed_to_me"] == 40.0

    fc = analytics.forecast_30d(conn, date(2026, 11, 10), {"Revolut": "Pessoal"},
                                {"Revolut": {"balance": 500.0}}, [])
    assert fc["now"] == 500.0 and fc["fixed_due"] == 0.0
