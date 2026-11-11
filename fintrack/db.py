"""Base de dados SQLite local (ficheiro data/fintrack.db). Nada sai do teu computador."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from .categories import categorize, merchant_key
from .importers import ImportResult

DEFAULT_PATH = Path(os.environ.get("FINTRACK_DB", Path(__file__).resolve().parent.parent / "data" / "fintrack.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    added INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    balance REAL,
    balance_date TEXT,
    kind TEXT NOT NULL DEFAULT '',
    merchant TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    category_manual INTEGER NOT NULL DEFAULT 0,
    hash TEXT NOT NULL UNIQUE,
    import_id INTEGER REFERENCES imports(id)
);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    name TEXT NOT NULL,
    isin TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL,
    currency TEXT NOT NULL,
    value_eur REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS account_groups (
    account TEXT PRIMARY KEY,
    grupo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_costs (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS valuations (
    id INTEGER PRIMARY KEY,
    asset TEXT NOT NULL,
    grupo TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS investment_txns (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    product TEXT NOT NULL,
    isin TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL,
    total_eur REAL NOT NULL,
    fees_eur REAL NOT NULL DEFAULT 0,
    order_id TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL UNIQUE
);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or DEFAULT_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Acrescenta colunas novas a bases de dados criadas por versões anteriores."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "balance_date" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN balance_date TEXT")
        conn.execute("UPDATE transactions SET balance_date = date")
        conn.commit()


def _hash(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def user_rules(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    # Padrões mais longos primeiro (mais específicos)
    rows = conn.execute("SELECT pattern, category FROM rules ORDER BY length(pattern) DESC").fetchall()
    return [(r["pattern"], r["category"]) for r in rows]


def save_import(conn: sqlite3.Connection, result: ImportResult, filename: str) -> dict:
    """Guarda o resultado de um import. Movimentos repetidos são ignorados."""
    cur = conn.execute("INSERT INTO imports (filename, source) VALUES (?, ?)", (filename, result.source))
    import_id = cur.lastrowid
    rules = user_rules(conn)
    added = duplicates = 0

    seen: Counter = Counter()
    for t in result.transactions:
        base = (t.account, t.date.isoformat(), f"{t.amount:.2f}", t.description, t.balance)
        seen[base] += 1  # distingue movimentos idênticos no mesmo dia
        h = _hash(*base, seen[base])
        cur = conn.execute(
            """INSERT OR IGNORE INTO transactions
               (account, date, description, amount, currency, balance, balance_date, kind, merchant, category,
                hash, import_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.account, t.date.isoformat(), t.description, t.amount, t.currency, t.balance,
                (t.balance_date or t.date).isoformat(),
                t.kind, merchant_key(t.description),
                categorize(t.description, t.amount, t.kind, rules), h, import_id,
            ),
        )
        if cur.rowcount:
            added += 1
        else:
            duplicates += 1

    if result.positions:
        today = date.today().isoformat()
        conn.execute("DELETE FROM positions WHERE snapshot_date = ?", (today,))
        conn.executemany(
            """INSERT INTO positions (snapshot_date, name, isin, quantity, price, currency, value_eur)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(today, p.name, p.isin, p.quantity, p.price, p.currency, p.value_eur) for p in result.positions],
        )
        added += len(result.positions)

    for it in result.investment_txns:
        h = _hash(it.date.isoformat(), it.isin, it.quantity, f"{it.total_eur:.2f}", it.order_id)
        cur = conn.execute(
            """INSERT OR IGNORE INTO investment_txns
               (date, product, isin, quantity, price, total_eur, fees_eur, order_id, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (it.date.isoformat(), it.product, it.isin, it.quantity, it.price, it.total_eur, it.fees_eur, it.order_id, h),
        )
        if cur.rowcount:
            added += 1
        else:
            duplicates += 1

    conn.execute("UPDATE imports SET added = ?, duplicates = ? WHERE id = ?", (added, duplicates, import_id))
    conn.commit()
    return {"added": added, "duplicates": duplicates, "skipped": result.skipped, "source": result.source}


def set_category(conn: sqlite3.Connection, txn_id: int, category: str, remember: bool = True) -> int:
    """Muda a categoria de um movimento. Com remember=True cria uma regra para o comerciante
    e aplica-a a todos os outros movimentos dele que não foram mudados à mão.
    Devolve o número de movimentos alterados."""
    row = conn.execute("SELECT merchant FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if row is None:
        return 0
    conn.execute("UPDATE transactions SET category = ?, category_manual = 1 WHERE id = ?", (category, txn_id))
    changed = 1
    merchant = row["merchant"]
    if remember and merchant:
        conn.execute(
            "INSERT INTO rules (pattern, category) VALUES (?, ?) "
            "ON CONFLICT(pattern) DO UPDATE SET category = excluded.category",
            (merchant, category),
        )
        cur = conn.execute(
            "UPDATE transactions SET category = ? WHERE merchant = ? AND category_manual = 0 AND id != ?",
            (category, merchant, txn_id),
        )
        changed += cur.rowcount
    conn.commit()
    return changed


def recategorize_all(conn: sqlite3.Connection) -> int:
    """Volta a aplicar as regras a todos os movimentos não editados à mão."""
    rules = user_rules(conn)
    rows = conn.execute(
        "SELECT id, description, amount, kind, category FROM transactions WHERE category_manual = 0"
    ).fetchall()
    changed = 0
    for r in rows:
        new = categorize(r["description"], r["amount"], r["kind"], rules)
        if new != r["category"]:
            conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (new, r["id"]))
            changed += 1
    conn.commit()
    return changed
