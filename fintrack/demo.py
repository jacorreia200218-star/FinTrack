"""Gera extratos FICTÍCIOS (Revolut, ActivoBank, DEGIRO) para experimentar a app.

    python -m fintrack.demo            # cria ficheiros em data/demo/ e importa-os
    python -m fintrack.demo --no-import

Os ficheiros imitam os formatos reais, por isso também servem para testar os importadores.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "demo"


def _month_days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def generate(out: Path = OUT, months: int = 8, seed: int = 7, today: date | None = None) -> list[Path]:
    rnd = random.Random(seed)
    today = today or date.today()
    start = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    out.mkdir(parents=True, exist_ok=True)

    # ---------- ActivoBank: salário, renda, contas da casa, top-ups para a Revolut ----------
    bank_rows = []
    for d in _month_days(start, today):
        if d.day == 25:
            bank_rows.append((d, "TRF VENCIMENTO EMPRESA EXEMPLO LDA", 2100.00))
        if d.day == 1:
            bank_rows.append((d, "TRF RENDA SENHORIO", -650.00))
        if d.day == 8:
            bank_rows.append((d, "DD EDP COMERCIAL", -round(rnd.uniform(38, 62), 2)))
        if d.day == 12:
            bank_rows.append((d, "DD VODAFONE PORTUGAL", -32.99))
        if d.day == 15:
            bank_rows.append((d, "DD FIDELIDADE SEGURO AUTO", -27.40))
        if d.day == 26:
            bank_rows.append((d, "TRF P/ REVOLUT", -1000.00))
        if d.day == 27:
            bank_rows.append((d, "TRF DEGIRO INVESTIMENTO", -200.00))
        if d.day in (5, 19) and rnd.random() < 0.6:
            bank_rows.append((d, "LEV. ATM LISBOA", -40.00))
    bank_rows.sort()
    balance = 2300.0
    bank_path = out / "activobank_movimentos.csv"
    with bank_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Conta:", "PT50 0000 0000 0000 0000 0000 0"])
        w.writerow(["Período:", f"{start:%d-%m-%Y} a {today:%d-%m-%Y}"])
        w.writerow([])
        w.writerow(["Data Lanc.", "Data Valor", "Descrição", "Montante", "Saldo"])
        for d, desc, amt in bank_rows:
            balance += amt
            fmt = lambda x: f"{x:.2f}".replace(".", ",")
            w.writerow([d.strftime("%d-%m-%Y"), d.strftime("%d-%m-%Y"), desc, fmt(amt), fmt(balance)])

    # ---------- Revolut: dia-a-dia com cartão ----------
    rev_rows = []
    groceries = ["Pingo Doce", "Continente", "Lidl", "Mercadona"]
    food = ["Padaria Portuguesa", "Restaurante O Tasco", "McDonald's", "Starbucks", "Sushi Time"]
    for d in _month_days(start, today):
        dt = datetime.combine(d, datetime.min.time()) + timedelta(hours=rnd.randint(8, 21), minutes=rnd.randint(0, 59))
        if d.day == 26:
            rev_rows.append((dt, "TOPUP", "Top-up by *1234", 1000.00))
        if rnd.random() < 0.35:
            rev_rows.append((dt, "CARD_PAYMENT", rnd.choice(groceries), -round(rnd.uniform(8, 65), 2)))
        if rnd.random() < 0.30:
            # Restauração mais cara no mês corrente, para gerar um alerta
            factor = 1.8 if d.month == today.month and d.year == today.year else 1.0
            rev_rows.append((dt + timedelta(minutes=5), "CARD_PAYMENT", rnd.choice(food), -round(rnd.uniform(4, 22) * factor, 2)))
        if rnd.random() < 0.10:
            rev_rows.append((dt + timedelta(minutes=9), "CARD_PAYMENT", "Uber", -round(rnd.uniform(5, 15), 2)))
        if rnd.random() < 0.06:
            rev_rows.append((dt + timedelta(minutes=12), "CARD_PAYMENT", "Uber Eats", -round(rnd.uniform(12, 28), 2)))
        if rnd.random() < 0.05:
            rev_rows.append((dt + timedelta(minutes=15), "CARD_PAYMENT", rnd.choice(["Amazon", "Worten", "Zara"]), -round(rnd.uniform(15, 90), 2)))
        if rnd.random() < 0.07:
            rev_rows.append((dt + timedelta(minutes=20), "CARD_PAYMENT", "Galp", -round(rnd.uniform(35, 55), 2)))
        if d.day == 3:
            rev_rows.append((dt, "CARD_PAYMENT", "Netflix", -13.99))
        if d.day == 10:
            rev_rows.append((dt, "CARD_PAYMENT", "Spotify", -8.99))
        if d.day == 2:
            rev_rows.append((dt, "CARD_PAYMENT", "Fitness Hut", -34.90))
        if d.day == 18:
            rev_rows.append((dt, "CARD_PAYMENT", "Apple.com/bill", -2.99))
        # Subscrição cancelada há uns meses
        if d.day == 20 and d < today - timedelta(days=100):
            rev_rows.append((dt, "CARD_PAYMENT", "Disney Plus", -8.99))
    rev_rows.sort()
    balance = 300.0
    rev_path = out / "revolut_extrato.csv"
    with rev_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Type", "Product", "Started Date", "Completed Date", "Description", "Amount", "Fee", "Currency", "State", "Balance"])
        for dt, kind, desc, amt in rev_rows:
            balance += amt
            stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            w.writerow([kind, "Current", stamp, stamp, desc, f"{amt:.2f}", "0.00", "EUR", "COMPLETED", f"{balance:.2f}"])
        w.writerow(["CARD_PAYMENT", "Current", stamp, "", "Compra recusada", "-99.00", "0.00", "EUR", "DECLINED", ""])

    # ---------- DEGIRO: compra mensal de 2 ETFs ----------
    etfs = [
        ("VANGUARD FTSE ALL-WORLD UCITS ETF USD ACC", "IE00BK5BQT80", 105.0, 0.012),
        ("ISHARES CORE MSCI EMERGING MARKETS IMI UCITS ETF", "IE00BKM4GZ66", 30.0, 0.006),
    ]
    tx_path = out / "degiro_Transactions.csv"
    holdings = {isin: 0.0 for _, isin, _, _ in etfs}
    prices = {isin: p for _, isin, p, _ in etfs}
    with tx_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Data", "Hora", "Produto", "ISIN", "Bolsa de referência", "Bolsa", "Quantidade", "Preços", "",
                    "Valor local", "", "Valor", "", "Taxa de Câmbio", "Custos de transação e/ou taxas de terceiros", "",
                    "Total", "", "ID da Ordem"])
        m = start
        n = 0
        while m <= today:
            d = m.replace(day=min(28, 28))
            if d <= today:
                for name, isin, _, drift in etfs:
                    prices[isin] *= 1 + drift + rnd.uniform(-0.03, 0.03)
                    budget = 160 if "ALL-WORLD" in name else 40
                    qty = max(1, int(budget // prices[isin]))
                    value = round(qty * prices[isin], 2)
                    fee = 1.00
                    holdings[isin] += qty
                    n += 1
                    w.writerow([d.strftime("%d-%m-%Y"), "09:05", name, isin, "EAM", "XET", qty, f"{prices[isin]:.2f}", "EUR",
                                f"{-value:.2f}", "EUR", f"{-value:.2f}", "EUR", "", f"{-fee:.2f}", "EUR",
                                f"{-(value + fee):.2f}", "EUR", f"demo-{n:04d}"])
            m = (m + timedelta(days=32)).replace(day=1)

    pf_path = out / "degiro_Portfolio.csv"
    with pf_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Produto", "Ticker/ISIN", "Quantidade", "Preço de fecho", "Valor local", "", "Valor em EUR"])
        w.writerow(["CASH & CASH FUND & FTX CASH (EUR)", "", "", "", "EUR", "23.41", "23.41"])
        for name, isin, _, _ in etfs:
            price = prices[isin] * 1.01
            value = holdings[isin] * price
            w.writerow([name, isin, f"{holdings[isin]:.0f}", f"{price:.2f}", "EUR", f"{value:.2f}", f"{value:.2f}"])

    return [bank_path, rev_path, tx_path, pf_path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-import", action="store_true", help="só gera os ficheiros")
    args = parser.parse_args()

    paths = generate()
    print("Ficheiros fictícios criados:")
    for p in paths:
        print("  ", p.relative_to(ROOT))
    if args.no_import:
        return

    from . import db
    from .importers import parse_file

    conn = db.connect()
    for p in paths:
        summary = db.save_import(conn, parse_file(p.read_bytes(), p.name), p.name)
        print(f"   importado {p.name}: {summary['added']} novos, {summary['duplicates']} repetidos")
    print("\nAgora corre:  python -m fintrack   e abre http://localhost:8000")


if __name__ == "__main__":
    main()
