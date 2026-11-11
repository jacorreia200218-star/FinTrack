from datetime import date
from pathlib import Path

import pytest

from fintrack.importers import UnknownFormat, parse_file
from fintrack.importers.common import parse_amount, parse_date

FIX = Path(__file__).parent / "fixtures"


def load(name, **kw):
    return parse_file((FIX / name).read_bytes(), name, **kw)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12,50", 12.5),
        ("-1.234,56", -1234.56),
        ("1,234.56", 1234.56),
        ("-12,50 €", -12.5),
        ("(12.50)", -12.5),
        ("2.100,00", 2100.0),
        ("1.234.567", 1234567.0),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_date():
    assert parse_date("2026-09-03 08:00:00") == date(2026, 9, 3)
    assert parse_date("03-09-2026") == date(2026, 9, 3)
    assert parse_date("03/09/2026") == date(2026, 9, 3)
    assert parse_date("") is None


def test_revolut_en():
    r = load("revolut_en.csv")
    assert r.source == "revolut"
    assert r.skipped == 1  # pendente
    assert len(r.transactions) == 5
    pingo = r.transactions[1]
    assert pingo.date == date(2026, 9, 2)  # usa a data da compra, não da cobrança
    assert pingo.balance_date == date(2026, 9, 3)
    assert pingo.amount == -23.45
    assert pingo.balance == 476.55
    assert pingo.kind == "CARD_PAYMENT"
    exchange = r.transactions[3]
    assert exchange.amount == -100.50  # inclui a comissão
    assert r.transactions[4].account == "Revolut USD"


def test_revolut_pt():
    r = load("revolut_pt.csv")
    assert r.source == "revolut"
    assert [t.amount for t in r.transactions] == [-41.20, -13.99]
    assert r.transactions[0].account == "Revolut"


def test_revolut_pt_app_export():
    """Formato real da app em português: tipos traduzidos, subcontas de poupança, cripto."""
    from fintrack.categories import categorize

    r = load("revolut_pt_app.csv")
    by_desc = {(t.account, t.description[:20]): t for t in r.transactions}
    assert len(r.transactions) == 12

    via_verde = by_desc[("Revolut", "Via Verde")]
    assert via_verde.date == date(2026, 9, 17) and via_verde.kind == "CARD_PAYMENT"

    accounts = {t.account for t in r.transactions}
    assert accounts == {"Revolut", "Revolut Poupança A", "Revolut Poupança B"}
    juros = by_desc[("Revolut Poupança A", "Pagamento de juros l")]
    assert juros.amount == 0.07 and juros.kind == "INTEREST"  # líquido de impostos

    cats = {t.description[:20]: categorize(t.description, t.amount, t.kind) for t in r.transactions}
    assert cats["Conversão cambial pa"] == "Investimento"
    assert cats["Carregamento com App"] == "Transferências internas"
    assert cats["De EUR Poupança A"] == "Transferências internas"
    assert cats["Para EUR Poupança B"] == "Transferências internas"
    assert cats["Apple"] == "Subscrições"
    assert cats["Pagamento de juros l"] == "Rendimento"


def test_activobank_with_preamble():
    r = load("activobank.csv", bank_account_name="ActivoBank")
    assert r.source == "bank"
    assert len(r.transactions) == 3
    assert r.skipped == 1  # linha de saldo final
    assert r.transactions[0].amount == -650.0
    assert r.transactions[2].amount == 2100.0
    assert r.transactions[2].balance == 3404.88
    assert all(t.account == "ActivoBank" for t in r.transactions)


def test_bank_debit_credit_columns():
    r = load("bank_debit_credit.csv", bank_account_name="Millennium")
    assert [t.amount for t in r.transactions] == [-12.34, 50.0]
    assert r.transactions[0].balance == 987.66


def test_degiro_portfolio():
    r = load("degiro_portfolio_en.csv")
    assert r.source == "degiro_portfolio"
    cash, etf = r.positions
    assert cash.isin == "CASH" and cash.value_eur == 12.5
    assert etf.isin == "IE00BK5BQT80"
    assert etf.quantity == 10 and etf.value_eur == 1205.0


def test_degiro_portfolio_pt_2026():
    r = load("degiro_portfolio_pt_2026.csv")
    cash, etf = r.positions
    assert cash.isin == "CASH" and cash.value_eur == 10.0
    assert etf.isin == "IE00B4L5Y983"
    assert etf.quantity == 2 and etf.price == 100.5 and etf.value_eur == 201.0


def test_degiro_transactions():
    r = load("degiro_transactions_pt.csv")
    assert r.source == "degiro_transactions"
    assert len(r.investment_txns) == 2
    t = r.investment_txns[0]
    assert t.quantity == 6 and t.price == 110.0
    assert t.total_eur == -661.0 and t.fees_eur == -1.0
    assert t.order_id == "abc-1"


def test_unknown_format():
    with pytest.raises(UnknownFormat):
        parse_file(b"foo,bar\n1,2\n", "x.csv")


def test_xlsx(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Data Lanc.", "Descrição", "Montante", "Saldo"])
    ws.append([date(2026, 9, 1), "PINGO DOCE", -10.5, 100])
    path = tmp_path / "mov.xlsx"
    wb.save(path)
    r = parse_file(path.read_bytes(), "mov.xlsx")
    assert r.transactions[0].amount == -10.5
    assert r.transactions[0].date == date(2026, 9, 1)
