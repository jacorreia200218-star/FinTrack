"""Importadores de extratos. Cada um reconhece o seu formato e devolve um ImportResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Txn:
    account: str
    date: date
    description: str
    amount: float  # negativo = saída de dinheiro
    currency: str = "EUR"
    balance: float | None = None
    kind: str = ""  # tipo original (ex.: CARD_PAYMENT, TOPUP na Revolut)
    balance_date: date | None = None  # quando o saldo foi registado (se diferente de date)


@dataclass
class Position:
    name: str
    isin: str
    quantity: float
    price: float | None
    currency: str
    value_eur: float


@dataclass
class InvestmentTxn:
    date: date
    product: str
    isin: str
    quantity: float
    price: float | None
    total_eur: float  # negativo = compra (dinheiro que saiu)
    fees_eur: float
    order_id: str = ""


@dataclass
class ImportResult:
    source: str  # 'revolut', 'bank', 'degiro_portfolio', 'degiro_transactions'
    transactions: list[Txn] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    investment_txns: list[InvestmentTxn] = field(default_factory=list)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


class UnknownFormat(ValueError):
    pass


def parse_file(content: bytes, filename: str, bank_account_name: str = "ActivoBank") -> ImportResult:
    """Deteta o formato do ficheiro e importa-o."""
    from . import bank, degiro, revolut
    from .common import read_rows

    rows = read_rows(content, filename)
    if not rows:
        raise UnknownFormat("O ficheiro está vazio.")

    for module in (revolut, degiro):
        result = module.try_parse(rows)
        if result is not None:
            return result

    result = bank.try_parse(rows, bank_account_name)
    if result is not None:
        return result

    raise UnknownFormat(
        "Não reconheci o formato deste ficheiro. Envia-me um exemplo (com dados "
        "anonimizados) para eu acrescentar suporte."
    )
