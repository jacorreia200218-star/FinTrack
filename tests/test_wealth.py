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
