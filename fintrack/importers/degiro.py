"""Exportações da DEGIRO (site > Portfólio > Exportar, e Caixa/Transações > Exportar).

Portfolio.csv (PT): Produto,Ticker/ISIN,Quantidade,Preço de fecho,Valor local,,Valor em EUR
Portfolio.csv (EN): Product,Symbol/ISIN,Amount,Closing,Local value,,Value in EUR
  -> 'Valor local' ocupa duas colunas: moeda e valor.

Transactions.csv (PT): Data,Hora,Produto,ISIN,Bolsa de referência,Bolsa,Quantidade,Preços,,
  Valor local,,Valor,,Taxa de Câmbio,Custos de transação e/ou taxas de terceiros,,Total,,ID da Ordem
  -> várias colunas são seguidas de uma coluna sem nome com a moeda.
"""

from __future__ import annotations

from . import ImportResult, InvestmentTxn, Position
from .common import cell, find_col, normalize, parse_amount, parse_date


def try_parse(rows: list[list[str]]) -> ImportResult | None:
    header = rows[0]
    norm = [normalize(h) for h in header]
    has_isin = any("isin" in h for h in norm)
    if not has_isin:
        return None
    if any(h in ("id da ordem", "order id") for h in norm):
        return _parse_transactions(rows)
    if any(h in ("valor em eur", "value in eur") for h in norm):
        return _parse_portfolio(rows)
    return None


def _parse_portfolio(rows: list[list[str]]) -> ImportResult:
    header = rows[0]
    c_name = find_col(header, "produto", "product", exact=True)
    c_isin = find_col(header, "ticker/isin", "symbol/isin", "isin")
    c_qty = find_col(header, "quantidade", "amount", "quantity", exact=True)
    c_price = find_col(header, "preco de fecho", "closing")
    c_local = find_col(header, "valor local", "local value")
    c_eur = find_col(header, "valor em eur", "value in eur")

    result = ImportResult(source="degiro_portfolio")
    for row in rows[1:]:
        if not any(row):
            continue
        value = parse_amount(cell(row, c_eur))
        if value is None:
            result.skipped += 1
            continue
        isin = cell(row, c_isin)
        name = cell(row, c_name)
        is_cash = not isin or "cash" in normalize(name)
        result.positions.append(
            Position(
                name=name,
                isin="CASH" if is_cash else isin,
                quantity=parse_amount(cell(row, c_qty)) or 0.0,
                price=parse_amount(cell(row, c_price)),
                currency=cell(row, c_local) if c_local is not None else "EUR",
                value_eur=value,
            )
        )
    return result


def _parse_transactions(rows: list[list[str]]) -> ImportResult:
    header = rows[0]
    c_date = find_col(header, "data", "date", exact=True)
    c_product = find_col(header, "produto", "product", exact=True)
    c_isin = find_col(header, "isin", exact=True)
    c_qty = find_col(header, "quantidade", "quantity", exact=True)
    c_price = find_col(header, "precos", "preco", "price", exact=True)
    c_value = find_col(header, "valor", "value", exact=True)
    c_fees = find_col(header, "custos de transacao", "transaction", "custos")
    c_total = find_col(header, "total", exact=True)
    c_order = find_col(header, "id da ordem", "order id")

    result = ImportResult(source="degiro_transactions")
    for row in rows[1:]:
        if not any(row):
            continue
        d = parse_date(cell(row, c_date))
        qty = parse_amount(cell(row, c_qty))
        total = parse_amount(cell(row, c_total))
        if total is None:
            total = parse_amount(cell(row, c_value))
        if d is None or qty is None or total is None:
            result.skipped += 1
            continue
        result.investment_txns.append(
            InvestmentTxn(
                date=d,
                product=cell(row, c_product),
                isin=cell(row, c_isin),
                quantity=qty,
                price=parse_amount(cell(row, c_price)),
                total_eur=total,
                fees_eur=parse_amount(cell(row, c_fees)) or 0.0,
                order_id=cell(row, c_order),
            )
        )
    return result
