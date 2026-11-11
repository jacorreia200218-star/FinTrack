"""Análises: gastos por mês/categoria, alertas, comerciantes, património."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .categories import NON_SPENDING

_EXCLUDED = tuple(sorted(NON_SPENDING))
_EXCL_SQL = "category NOT IN (" + ",".join("?" * len(_EXCLUDED)) + ")"


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def last_months(n: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def monthly_totals(conn: sqlite3.Connection, months: list[str]) -> list[dict]:
    """Entradas e saídas por mês, sem contar transferências entre contas próprias."""
    rows = conn.execute(
        f"""SELECT substr(date, 1, 7) AS m,
                   SUM(CASE WHEN amount < 0 AND {_EXCL_SQL} THEN -amount ELSE 0 END) AS spent,
                   SUM(CASE WHEN amount > 0 AND category = 'Rendimento' THEN amount ELSE 0 END) AS income
            FROM transactions GROUP BY m""",
        _EXCLUDED,
    ).fetchall()
    by_month = {r["m"]: r for r in rows}
    return [
        {
            "month": m,
            "spent": round(by_month[m]["spent"], 2) if m in by_month else 0.0,
            "income": round(by_month[m]["income"], 2) if m in by_month else 0.0,
        }
        for m in months
    ]


def spending_by_category(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Gasto por categoria entre duas datas (ISO, end exclusivo)."""
    rows = conn.execute(
        f"""SELECT category, SUM(-amount) AS total, COUNT(*) AS n
            FROM transactions
            WHERE amount < 0 AND date >= ? AND date < ? AND {_EXCL_SQL}
            GROUP BY category ORDER BY total DESC""",
        (start, end, *_EXCLUDED),
    ).fetchall()
    return [{"category": r["category"], "total": round(r["total"], 2), "count": r["n"]} for r in rows]


def category_matrix(conn: sqlite3.Connection, months: list[str]) -> dict[str, dict[str, float]]:
    rows = conn.execute(
        f"""SELECT category, substr(date, 1, 7) AS m, SUM(-amount) AS total
            FROM transactions WHERE amount < 0 AND {_EXCL_SQL}
            GROUP BY category, m""",
        _EXCLUDED,
    ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["m"] in months:
            out.setdefault(r["category"], {})[r["m"]] = round(r["total"], 2)
    return out


def top_merchants(conn: sqlite3.Connection, start: str, end: str, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        f"""SELECT merchant, MAX(description) AS description, MAX(category) AS category,
                   SUM(-amount) AS total, COUNT(*) AS n
            FROM transactions
            WHERE amount < 0 AND date >= ? AND date < ? AND {_EXCL_SQL} AND merchant != ''
            GROUP BY merchant ORDER BY total DESC LIMIT ?""",
        (start, end, *_EXCLUDED, limit),
    ).fetchall()
    return [dict(r) | {"total": round(r["total"], 2)} for r in rows]


def alerts(conn: sqlite3.Connection, today: date | None = None, fixed_merchants: set[str] | None = None) -> list[dict]:
    """Compara o mês corrente com a média dos 3 meses anteriores, por categoria.

    A projeção até ao fim do mês só extrapola os gastos variáveis: as despesas fixas
    (fixed_merchants, ex.: renda paga no dia 1) contam pelo valor real.
    """
    today = today or date.today()
    fixed_merchants = fixed_merchants or set()
    months = last_months(4, today)
    current, previous = months[-1], months[:-1]
    matrix = category_matrix(conn, months)

    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    progress = today.day / (next_month - month_start).days

    fixed_now: dict[str, float] = {}
    variable_count: dict[str, int] = {}
    for r in conn.execute(
        f"""SELECT category, merchant, SUM(-amount) AS total, COUNT(*) AS n FROM transactions
            WHERE amount < 0 AND date >= ? AND date < ? AND {_EXCL_SQL}
            GROUP BY category, merchant""",
        (month_start.isoformat(), next_month.isoformat(), *_EXCLUDED),
    ):
        if r["merchant"] in fixed_merchants:
            fixed_now[r["category"]] = fixed_now.get(r["category"], 0.0) + r["total"]
        else:
            variable_count[r["category"]] = variable_count.get(r["category"], 0) + r["n"]

    out = []
    for category, by_month in matrix.items():
        hist = [by_month.get(m, 0.0) for m in previous]
        if sum(1 for v in hist if v > 0) < 2:
            continue
        avg = sum(hist) / len(hist)
        now = by_month.get(current, 0.0)
        fixed = fixed_now.get(category, 0.0)
        variable = now - fixed
        # Só projeta com dados suficientes: >= 30% do mês e >= 4 gastos variáveis
        can_project = progress >= 0.3 and variable_count.get(category, 0) >= 4
        projected = fixed + variable / progress if can_project else now

        if now > avg * 1.1 and now - avg > 15:
            out.append({
                "category": category, "current": round(now, 2), "average": round(avg, 2),
                "pct": round((now / avg - 1) * 100), "projected": False,
            })
        elif projected > avg * 1.3 and projected - avg > 25:
            out.append({
                "category": category, "current": round(now, 2), "average": round(avg, 2),
                "pct": round((projected / avg - 1) * 100), "projected": True,
                "projection": round(projected, 2),
            })
    out.sort(key=lambda a: -(a.get("projection", a["current"]) - a["average"]))
    return out


def account_balances(conn: sqlite3.Connection) -> list[dict]:
    """Saldo mais recente de cada conta (do próprio extrato)."""
    rows = conn.execute(
        """SELECT t.account, t.balance, t.balance_date AS date, t.currency FROM transactions t
           WHERE t.balance IS NOT NULL AND t.id = (
               SELECT t2.id FROM transactions t2
               WHERE t2.account = t.account AND t2.balance IS NOT NULL
               ORDER BY t2.balance_date DESC, t2.id DESC LIMIT 1)
           ORDER BY t.account"""
    ).fetchall()
    return [dict(r) for r in rows]


def portfolio(conn: sqlite3.Connection) -> dict:
    snap = conn.execute("SELECT MAX(snapshot_date) AS d FROM positions").fetchone()["d"]
    positions = []
    if snap:
        positions = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE snapshot_date = ? ORDER BY value_eur DESC", (snap,)
        )]
    invested_value = sum(p["value_eur"] for p in positions if p["isin"] != "CASH")
    cash = sum(p["value_eur"] for p in positions if p["isin"] == "CASH")

    inv = conn.execute(
        "SELECT COALESCE(SUM(-total_eur), 0) AS net, COALESCE(SUM(ABS(fees_eur)), 0) AS fees, COUNT(*) AS n "
        "FROM investment_txns"
    ).fetchone()
    net_invested = round(inv["net"], 2) if inv["n"] else None

    per_isin = {
        r["isin"]: r["net"] for r in conn.execute(
            "SELECT isin, SUM(-total_eur) AS net FROM investment_txns GROUP BY isin"
        )
    }
    for p in positions:
        cost = per_isin.get(p["isin"])
        p["cost"] = round(cost, 2) if cost is not None else None
        p["gain"] = round(p["value_eur"] - cost, 2) if cost is not None else None
        p["gain_pct"] = round((p["value_eur"] / cost - 1) * 100, 1) if cost else None
        p["weight"] = round(p["value_eur"] / invested_value * 100, 1) if invested_value and p["isin"] != "CASH" else None

    return {
        "snapshot_date": snap,
        "positions": positions,
        "value": round(invested_value, 2),
        "cash": round(cash, 2),
        "net_invested": net_invested,
        "gain": round(invested_value - net_invested, 2) if net_invested is not None else None,
        "fees": round(inv["fees"], 2),
    }


def net_worth(conn: sqlite3.Connection) -> dict:
    balances = account_balances(conn)
    eur_accounts = sum(b["balance"] for b in balances if b["currency"] == "EUR")
    pf = portfolio(conn)
    return {
        "accounts": round(eur_accounts, 2),
        "investments": round(pf["value"] + pf["cash"], 2),
        "total": round(eur_accounts + pf["value"] + pf["cash"], 2),
        "balances": balances,
    }


# ---------- património por grupos (Pessoal, Conjunta, Poupanças, Cripto, Investimentos) ----------
GROUPS = ["Pessoal", "Conjunta", "Poupanças", "Cripto", "Investimentos"]


def default_group(account: str) -> str:
    return "Poupanças" if "poupan" in account.lower() else "Pessoal"


def groups_for_accounts(conn: sqlite3.Connection) -> dict[str, str]:
    """Conta -> grupo. Usa a escolha do utilizador; se não houver, adivinha pelo nome."""
    names = [r[0] for r in conn.execute("SELECT DISTINCT account FROM transactions ORDER BY account")]
    overrides = {r["account"]: r["grupo"] for r in conn.execute("SELECT account, grupo FROM account_groups")}
    return {n: overrides.get(n, default_group(n)) for n in names}


def valuation_summary(conn: sqlite3.Connection) -> list[dict]:
    """Ativos registados à mão (PPR, Bitcoin...): valor atual, variação e histórico."""
    rows = conn.execute("SELECT asset, grupo, date, value, note, id FROM valuations ORDER BY asset, date, id").fetchall()
    assets: dict[str, dict] = {}
    for r in rows:
        a = assets.setdefault(r["asset"], {"asset": r["asset"], "grupo": r["grupo"], "history": []})
        a["history"].append({"id": r["id"], "date": r["date"], "value": r["value"], "note": r["note"]})
    out = []
    for a in assets.values():
        h = a["history"]
        first, last = h[0]["value"], h[-1]["value"]
        a.update(
            first=first,
            last=last,
            date=h[-1]["date"],
            change=round(last - first, 2),
            change_pct=round((last / first - 1) * 100, 1) if first else None,
        )
        out.append(a)
    return out


def btc_invested(conn: sqlite3.Connection) -> tuple[float, int]:
    """Total em euros que foi convertido em Bitcoin, segundo os extratos da Revolut."""
    row = conn.execute(
        "SELECT COALESCE(SUM(-amount), 0) AS s, COUNT(*) AS n FROM transactions "
        "WHERE kind = 'EXCHANGE' AND description LIKE '%BTC%'"
    ).fetchone()
    return round(row["s"], 2), row["n"]


def btc_cost(conn: sqlite3.Connection) -> dict:
    """Custo total do Bitcoin: o valor escrito à mão tem prioridade sobre o calculado pelos extratos."""
    manual = conn.execute("SELECT value FROM manual_costs WHERE key = 'bitcoin'").fetchone()
    if manual is not None:
        return {"value": round(manual["value"], 2), "source": "manual"}
    value, n = btc_invested(conn)
    return {"value": value, "source": f"extratos ({n} conversões)"}


# ---------- previsão, conjunta e histórico ----------
def forecast_30d(conn: sqlite3.Connection, today: date, groups_map: dict, balances: dict, recs: list) -> dict:
    """Saldo previsto daqui a 30 dias nas contas pessoais.

    Saldo de hoje + rendimento médio (3 meses) - despesas fixas que vencem - gasto variável médio.
    É uma estimativa: serve para antecipar, não para decidir com precisão."""
    from datetime import timedelta as _td
    from .recurring import step

    pessoal = [n for n, g in groups_map.items() if g == "Pessoal"]
    now = round(sum(balances[n]["balance"] for n in pessoal if n in balances), 2)

    months = last_months(4, today)[:-1]
    totals = monthly_totals(conn, months)
    avg_income = sum(t["income"] for t in totals) / len(totals)
    avg_spent = sum(t["spent"] for t in totals) / len(totals)
    active = [r for r in recs if r.active]
    monthly_fixed = sum(r.monthly_cost for r in active)
    variable_month = max(avg_spent - monthly_fixed, 0.0)

    horizon = today + _td(days=30)
    fixed_due = 0.0
    for r in active:
        d = r.next_date
        while d <= horizon:
            if d >= today:
                fixed_due += r.amount
            d = step(d, r)

    expected = round(now + avg_income - fixed_due - variable_month, 2)
    return {
        "now": now,
        "income": round(avg_income, 2),
        "fixed_due": round(fixed_due, 2),
        "variable": round(variable_month, 2),
        "expected": expected,
    }


def shared_summary(conn: sqlite3.Connection, today: date) -> dict:
    """Despesas marcadas como partilhadas nos últimos 60 dias e quanto o outro deve."""
    since = (today - timedelta(days=60)).isoformat()
    rows = conn.execute(
        """SELECT t.id, t.date, t.description, t.amount, t.category, s.my_percent
           FROM transactions t JOIN shared_splits s ON s.txn_id = t.id
           WHERE t.date >= ? AND t.amount < 0 ORDER BY t.date DESC""",
        (since,),
    ).fetchall()
    items = [dict(r) for r in rows]
    owed = round(sum(-i["amount"] * (1 - i["my_percent"] / 100) for i in items), 2)
    return {"rows": items, "owed_to_me": owed}


def record_net_worth(conn: sqlite3.Connection, today: date, total: float) -> None:
    conn.execute(
        "INSERT INTO net_worth (date, total) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET total = excluded.total",
        (today.isoformat(), round(total, 2)),
    )
    conn.commit()


def net_worth_history(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT date, total FROM net_worth ORDER BY date")]
