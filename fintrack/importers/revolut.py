"""Extrato de conta da Revolut (CSV exportado na app: Conta > Extrato > Excel/CSV).

Cabeçalho em inglês:   Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance
Cabeçalho em português: Tipo,Produto,Data de início,Data de Conclusão,Descrição,Montante,Comissão,Moeda,Estado,Saldo
"""

from __future__ import annotations

import re

from . import ImportResult, Txn
from .common import cell, find_col, normalize, parse_amount, parse_date

_OK_STATES = {"completed", "concluido", "concluida", "completo", "completa"}

# A app em português traduz o tipo; normalizamos para os códigos em inglês
_KIND_PT = {
    "pagamento com cartao": "CARD_PAYMENT",
    "cambio": "EXCHANGE",
    "transferencia": "TRANSFER",
    "carregamento": "TOPUP",
    "juros": "INTEREST",
    "levantamento": "ATM",
    "levantamento de numerario": "ATM",
    "reembolso": "REFUND",
    "reembolso do cartao": "CARD_REFUND",
    "comissao": "FEE",
    "cashback": "CASHBACK",
    "recompensa": "REWARD",
}

_POCKET_RE = re.compile(r'(?:subconta\s+"([^"]+)"|(?:de|para|from|to)\s+EUR\s+(.+)$)', re.IGNORECASE)


def _kind(raw: str) -> str:
    return _KIND_PT.get(normalize(raw), raw.strip().upper().replace(" ", "_"))


def _pocket(description: str) -> str | None:
    """Nome da subconta de poupança ('Poupança JC') a partir da descrição."""
    m = _POCKET_RE.search(description)
    if not m:
        return None
    return (m.group(1) or m.group(2)).strip()


def try_parse(rows: list[list[str]]) -> ImportResult | None:
    header = rows[0]
    norm = {normalize(h) for h in header}
    is_revolut = ("started date" in norm or "data de inicio" in norm) and (
        "product" in norm or "produto" in norm
    )
    if not is_revolut:
        return None

    c_type = find_col(header, "type", "tipo", exact=True)
    c_product = find_col(header, "product", "produto", exact=True)
    c_started = find_col(header, "started date", "data de inicio")
    c_completed = find_col(header, "completed date", "data de conclusao")
    c_desc = find_col(header, "description", "descricao")
    c_amount = find_col(header, "amount", "montante", exact=True)
    c_fee = find_col(header, "fee", "comissao", "taxa", exact=True)
    c_currency = find_col(header, "currency", "moeda", exact=True)
    c_state = find_col(header, "state", "estado", exact=True)
    c_balance = find_col(header, "balance", "saldo", exact=True)

    result = ImportResult(source="revolut")
    for row in rows[1:]:
        if not any(row):
            continue
        state = normalize(cell(row, c_state))
        if state and state not in _OK_STATES:
            result.skipped += 1  # pendentes, revertidas, recusadas
            continue
        # Data da compra (início), não da cobrança: a Via Verde, p.ex., só é cobrada semanas depois
        d = parse_date(cell(row, c_started)) or parse_date(cell(row, c_completed))
        amount = parse_amount(cell(row, c_amount))
        if d is None or amount is None:
            result.skipped += 1
            continue
        fee = parse_amount(cell(row, c_fee)) or 0.0
        currency = cell(row, c_currency) or "EUR"
        product = cell(row, c_product) or "Current"

        description = cell(row, c_desc)
        account = "Revolut"
        if normalize(product) not in ("current", "atual", "corrente", "conta corrente"):
            # Poupanças: cada subconta tem o seu saldo, por isso fica como conta separada
            pocket = _pocket(description) if normalize(product) in ("savings", "deposito", "poupanca") else None
            account += f" {pocket or product}"
        if currency != "EUR":
            account += f" {currency}"

        result.transactions.append(
            Txn(
                account=account,
                date=d,
                description=description,
                amount=round(amount - fee, 2),
                currency=currency,
                balance=parse_amount(cell(row, c_balance)),
                kind=_kind(cell(row, c_type)),
                balance_date=parse_date(cell(row, c_completed)),
            )
        )
    return result
