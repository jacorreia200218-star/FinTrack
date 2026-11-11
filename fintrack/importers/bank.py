"""Importador genérico para extratos de bancos portugueses (ActivoBank, Millennium, CGD...).

Os extratos costumam ter umas linhas de cabeçalho (nº de conta, período, etc.) antes da
tabela, por isso procuramos a linha que parece ser o cabeçalho da tabela de movimentos.
Suporta uma coluna de montante com sinal OU colunas separadas de débito/crédito.
"""

from __future__ import annotations

from . import ImportResult, Txn
from .common import cell, find_col, normalize, parse_amount, parse_date

DATE_COLS = ("data lancamento", "data mov", "data movimento", "data operacao", "data", "date")
DESC_COLS = ("descricao", "descritivo", "movimento", "description", "designacao", "detalhe")
AMOUNT_COLS = ("montante", "valor", "importancia", "amount")
DEBIT_COLS = ("debito", "debit")
CREDIT_COLS = ("credito", "credit")
BALANCE_COLS = ("saldo contabilistico", "saldo disponivel", "saldo", "balance")


def _find_header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:40]):
        has_date = find_col(row, *DATE_COLS) is not None
        has_desc = find_col(row, *DESC_COLS) is not None
        has_amount = find_col(row, *AMOUNT_COLS, *DEBIT_COLS) is not None
        if has_date and has_desc and has_amount:
            return i
    return None


def try_parse(rows: list[list[str]], account_name: str) -> ImportResult | None:
    h = _find_header(rows)
    if h is None:
        return None
    header = rows[h]

    c_date = find_col(header, *DATE_COLS)
    c_desc = find_col(header, *DESC_COLS)
    c_amount = find_col(header, *AMOUNT_COLS)
    c_debit = find_col(header, *DEBIT_COLS)
    c_credit = find_col(header, *CREDIT_COLS)
    c_balance = find_col(header, *BALANCE_COLS)
    c_currency = find_col(header, "moeda", "divisa", "currency", exact=True)
    # Evita que "valor" apanhe "data valor"
    if c_amount is not None and "data" in normalize(header[c_amount]):
        c_amount = None

    result = ImportResult(source="bank")
    for row in rows[h + 1 :]:
        if not any(row):
            continue
        d = parse_date(cell(row, c_date))
        if d is None:
            result.skipped += 1  # linhas de totais/rodapé
            continue
        if c_amount is not None:
            amount = parse_amount(cell(row, c_amount))
        else:
            debit = parse_amount(cell(row, c_debit))
            credit = parse_amount(cell(row, c_credit))
            if debit is None and credit is None:
                amount = None
            else:
                amount = (abs(credit) if credit else 0.0) - (abs(debit) if debit else 0.0)
        if amount is None:
            result.skipped += 1
            continue
        result.transactions.append(
            Txn(
                account=account_name,
                date=d,
                description=cell(row, c_desc),
                amount=round(amount, 2),
                currency=cell(row, c_currency) or "EUR",
                balance=parse_amount(cell(row, c_balance)),
            )
        )
    if not result.transactions and result.skipped:
        result.warnings.append("Encontrei a tabela mas nenhuma linha válida.")
    return result
