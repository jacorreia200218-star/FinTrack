"""Liga as contas do banco e importa os movimentos para a base de dados.

Fluxo:
  1. begin_link()   -> cria um 'state' aleatório e devolve o URL do banco
  2. (utilizador autoriza no banco e volta a /callback?code=...&state=...)
  3. finish_link()  -> troca o código por uma sessão e guarda as contas
  4. sync_all()     -> puxa os movimentos novos (corre todos os dias)
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import date, timedelta

from .. import db
from ..importers import ImportResult, Txn
from .enable import Client, EnableBankingError, is_configured, load_config

# chave interna -> (nome do banco na API, país, nome da conta na app)
# Os nomes na API ainda por confirmar para Portugal: ver GET /aspsps no painel do Enable Banking.
BANKS = {
    "revolut": ("Revolut", "PT", "Revolut"),
    "activobank": ("ActivoBank", "PT", "ActivoBank"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bank_links (
    id INTEGER PRIMARY KEY,
    bank TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    session_id TEXT,
    accounts TEXT NOT NULL DEFAULT '[]',
    valid_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_sync TEXT,
    last_error TEXT
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def begin_link(conn: sqlite3.Connection, client: Client, bank: str, redirect_url: str) -> str:
    if bank not in BANKS:
        raise ValueError("Banco desconhecido")
    api_name, country, _ = BANKS[bank]
    state = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO bank_links (bank, state) VALUES (?, ?)", (bank, state))
    conn.commit()
    resp = client.start_auth(api_name, country, redirect_url, state)
    return resp["url"]


def finish_link(conn: sqlite3.Connection, client: Client, state: str, code: str) -> str:
    row = conn.execute("SELECT * FROM bank_links WHERE state = ? AND status = 'pending'", (state,)).fetchone()
    if row is None:
        raise EnableBankingError("Ligação inválida ou já usada. Tenta de novo.")
    session = client.create_session(code)
    accounts = [a["uid"] for a in session.get("accounts", []) if a.get("uid")]
    valid_until = (session.get("access") or {}).get("valid_until")
    conn.execute(
        "UPDATE bank_links SET status = 'linked', session_id = ?, accounts = ?, valid_until = ?, "
        "last_sync = NULL, last_error = NULL, state = ? WHERE id = ?",
        (session.get("session_id"), json.dumps(accounts), valid_until, secrets.token_urlsafe(16), row["id"]),
    )
    conn.commit()
    return row["bank"]


def _to_txn(t: dict, account_name: str) -> Txn | None:
    if t.get("status", "BOOK") != "BOOK":
        return None  # só movimentos confirmados (ignora pendentes)
    amount = float(t["transaction_amount"]["amount"])
    if t.get("credit_debit_indicator") == "DBIT":
        amount = -abs(amount)
    else:
        amount = abs(amount)
    day = t.get("booking_date") or t.get("value_date") or t.get("transaction_date")
    if not day:
        return None
    remittance = " ".join(t.get("remittance_information") or [])
    counterpart = ""
    party = t.get("creditor") if amount < 0 else t.get("debtor")
    if party and party.get("name"):
        counterpart = party["name"]
    description = (remittance or counterpart or "Movimento").strip()
    return Txn(account=account_name, date=date.fromisoformat(day[:10]), description=description, amount=round(amount, 2))


def sync_link(conn: sqlite3.Connection, client: Client, link: sqlite3.Row) -> int:
    """Importa os movimentos novos de um banco ligado. Devolve quantos foram adicionados."""
    _, _, account_name = BANKS[link["bank"]]
    since = link["last_sync"] or link["created_at"][:10]
    date_from = (date.fromisoformat(since[:10]) - timedelta(days=2)).isoformat()  # sobreposição; a hash evita duplicados
    txns: list[Txn] = []
    for uid in json.loads(link["accounts"]):
        key = None
        while True:
            page = client.transactions(uid, date_from, key)
            for raw in page.get("transactions", []):
                t = _to_txn(raw, account_name)
                if t:
                    txns.append(t)
            key = page.get("continuation_key")
            if not key:
                break
    summary = db.save_import(conn, ImportResult(source="bank_api", transactions=txns), f"API {account_name}")
    conn.execute(
        "UPDATE bank_links SET last_sync = ?, last_error = NULL WHERE id = ?",
        (date.today().isoformat(), link["id"]),
    )
    conn.commit()
    return summary["added"]


def sync_all(conn: sqlite3.Connection, client: Client | None = None) -> list[dict]:
    ensure_schema(conn)
    client = client or Client()
    results = []
    for link in conn.execute("SELECT * FROM bank_links WHERE status = 'linked'").fetchall():
        try:
            added = sync_link(conn, client, link)
            results.append({"bank": link["bank"], "ok": True, "added": added})
        except EnableBankingError as e:
            msg = str(e)
            # 401/403 costuma significar que o consentimento expirou: pede nova ligação
            status = "expired" if msg.startswith(("401", "403")) else "linked"
            conn.execute(
                "UPDATE bank_links SET status = ?, last_error = ? WHERE id = ?",
                (status, msg[:300], link["id"]),
            )
            conn.commit()
            results.append({"bank": link["bank"], "ok": False, "error": msg})
    return results


def main() -> None:
    """Chamado pelo script diário: python -m fintrack.bank.sync"""
    if not is_configured(load_config()):
        print("Enable Banking não configurado; nada a fazer.")
        return
    conn = db.connect()
    for r in sync_all(conn):
        print(r)


if __name__ == "__main__":
    main()
