from datetime import date, timedelta
from pathlib import Path

import pytest

from fintrack import analytics, db, recurring
from fintrack.categories import categorize, merchant_key
from fintrack.importers import ImportResult, Txn, parse_file

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.mark.parametrize(
    "desc, amount, kind, expected",
    [
        ("Pingo Doce", -20, "CARD_PAYMENT", "Supermercado"),
        ("COMPRA 4567 LIDL LISBOA", -12, "", "Supermercado"),
        ("Uber Eats", -15, "CARD_PAYMENT", "Takeaway"),
        ("Uber", -8, "CARD_PAYMENT", "Transportes"),
        ("Netflix", -13.99, "CARD_PAYMENT", "Subscrições"),
        ("DD EDP COMERCIAL", -45, "", "Casa e Utilidades"),
        ("Galp", -40, "CARD_PAYMENT", "Combustível"),
        ("TRF VENCIMENTO EMPRESA", 2100, "", "Rendimento"),
        ("Top-up by *1234", 500, "TOPUP", "Transferências internas"),
        ("TRF P/ REVOLUT", -500, "", "Transferências internas"),
        ("TRF DEGIRO", -200, "", "Investimento"),
        ("Exchanged to USD", -100, "EXCHANGE", "Câmbio"),
        ("Takis Snacks", -2, "CARD_PAYMENT", "Outros"),  # 'aki' não pode apanhar 'takis'
        ("Loja qualquer", -5, "", "Outros"),
    ],
)
def test_categorize(desc, amount, kind, expected):
    assert categorize(desc, amount, kind) == expected


def test_user_rules_take_priority():
    assert categorize("Loja do Bairro", -5, "", [("loja do bairro", "Lazer")]) == "Lazer"


def test_merchant_key_groups_variants():
    assert merchant_key("COMPRA 1234 PINGO DOCE LISBOA 12/03") == merchant_key("Pingo Doce Lisboa")


def test_import_is_idempotent(conn):
    content = (FIX / "revolut_en.csv").read_bytes()
    first = db.save_import(conn, parse_file(content, "r.csv"), "r.csv")
    second = db.save_import(conn, parse_file(content, "r.csv"), "r.csv")
    assert first["added"] == 5
    assert second["added"] == 0 and second["duplicates"] == 5


def test_identical_same_day_transactions_kept(conn):
    t = Txn(account="Revolut", date=date(2026, 9, 1), description="Café", amount=-0.8)
    db.save_import(conn, ImportResult(source="revolut", transactions=[t, t]), "x")
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2


def test_set_category_learns(conn):
    txns = [
        Txn(account="A", date=date(2026, 9, d), description=f"COMPRA {d} LOJA DO BAIRRO", amount=-5)
        for d in (1, 2, 3)
    ]
    db.save_import(conn, ImportResult(source="bank", transactions=txns), "x")
    first_id = conn.execute("SELECT id FROM transactions ORDER BY id LIMIT 1").fetchone()[0]
    changed = db.set_category(conn, first_id, "Lazer")
    assert changed == 3
    # Novos movimentos do mesmo comerciante já entram com a nova categoria
    new = Txn(account="A", date=date(2026, 9, 9), description="COMPRA 9 LOJA DO BAIRRO", amount=-7)
    db.save_import(conn, ImportResult(source="bank", transactions=[new]), "y")
    cats = {r[0] for r in conn.execute("SELECT category FROM transactions")}
    assert cats == {"Lazer"}


def _monthly(desc, amount, months, day=5, end=date(2026, 11, 1), jitter=None):
    out = []
    for i in range(months):
        y, m = end.year, end.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        a = amount + (jitter[i] if jitter else 0)
        out.append(Txn(account="A", date=date(y, m, day), description=desc, amount=-a))
    return out


def test_recurring_detection(conn):
    txns = (
        _monthly("Netflix", 13.99, 6, day=3)
        + _monthly("Ginasio Fitness Hut", 34.90, 5, day=2)
        + _monthly("DD EDP COMERCIAL", 45, 6, day=8, jitter=[0, 5, -4, 3, 7, -2])
        # Supermercado: frequente mas irregular — não é despesa fixa
        + [Txn(account="A", date=date(2026, 10, 1) + timedelta(days=d), description="Pingo Doce", amount=-(10 + d))
           for d in (0, 2, 3, 7, 8, 15, 20)]
        # Cancelada: parou em julho
        + _monthly("Disney Plus", 8.99, 4, day=20, end=date(2026, 7, 1))
    )
    db.save_import(conn, ImportResult(source="bank", transactions=txns), "x")
    recs = {r.description: r for r in recurring.detect(conn, today=date(2026, 11, 10))}

    assert "Pingo Doce" not in recs
    assert recs["Netflix"].period == "Mensal" and recs["Netflix"].amount == 13.99
    assert recs["Netflix"].active and recs["Netflix"].next_date == date(2026, 12, 3)
    assert recs["DD EDP COMERCIAL"].active
    assert not recs["Disney Plus"].active

    fc = recurring.month_forecast(list(recs.values()), today=date(2026, 11, 10))
    assert fc["monthly_fixed"] == pytest.approx(13.99 + 34.90 + recs["DD EDP COMERCIAL"].monthly_cost, abs=0.5)


def test_alerts_and_totals(conn):
    txns = []
    for m in (8, 9, 10):
        txns.append(Txn(account="A", date=date(2026, m, 10), description="Restaurante X", amount=-100))
    txns.append(Txn(account="A", date=date(2026, 11, 3), description="Restaurante X", amount=-250))
    txns.append(Txn(account="A", date=date(2026, 11, 4), description="Top-up", amount=500, kind="TOPUP"))
    txns.append(Txn(account="A", date=date(2026, 11, 1), description="TRF VENCIMENTO", amount=2000))
    db.save_import(conn, ImportResult(source="bank", transactions=txns), "x")

    al = analytics.alerts(conn, today=date(2026, 11, 10))
    assert al and al[0]["category"] == "Restauração" and al[0]["pct"] == 150

    totals = analytics.monthly_totals(conn, ["2026-11"])
    assert totals[0]["spent"] == 250  # top-up não conta
    assert totals[0]["income"] == 2000


def test_balances_use_latest_settled_movement(conn):
    """Com a data da compra, um movimento antigo cobrado tarde não pode esconder o saldo final."""
    db.save_import(conn, parse_file((FIX / "revolut_pt_app.csv").read_bytes(), "r.csv"), "r.csv")
    bal = {b["account"]: b["balance"] for b in analytics.account_balances(conn)}
    assert bal == {"Revolut": 185.44, "Revolut Poupança A": 900.07, "Revolut Poupança B": 525.02}
    # Transferências entre conta e poupanças e compras de BTC não contam como gasto
    oct_total = analytics.monthly_totals(conn, ["2026-10"])[0]["spent"]
    assert oct_total == round(22.99 + 3.99 + 12.38, 2)


def test_portfolio(conn):
    for name in ("degiro_portfolio_en.csv", "degiro_transactions_pt.csv"):
        db.save_import(conn, parse_file((FIX / name).read_bytes(), name), name)
    pf = analytics.portfolio(conn)
    assert pf["value"] == 1205.0
    assert pf["cash"] == 12.5
    assert pf["net_invested"] == 1122.0
    assert pf["gain"] == 83.0
    etf = next(p for p in pf["positions"] if p["isin"] != "CASH")
    assert etf["gain_pct"] == pytest.approx(7.4, abs=0.1)
