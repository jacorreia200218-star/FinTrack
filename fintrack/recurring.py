"""Deteção de despesas recorrentes (renda, subscrições, ginásio, telemóvel...) e previsão."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from .categories import NON_SPENDING

PERIODS = [
    # (nome, intervalo mínimo em dias, máximo, meses por período (0 = semanal), vezes por mês)
    ("Semanal", 5, 9, 0, 52 / 12),
    ("Mensal", 26, 35, 1, 1.0),
    ("Trimestral", 85, 95, 3, 1 / 3),
    ("Anual", 350, 380, 12, 1 / 12),
]


def add_months(d: date, months: int, day: int | None = None) -> date:
    """Soma meses mantendo o dia (31 jan + 1 mês = 28/29 fev)."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    next_first = date(y + (m == 12), m % 12 + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return date(y, m, min(day or d.day, last_day))


def step(d: date, rec: "Recurring") -> date:
    if rec.months:
        return add_months(d, rec.months, rec.day_of_month)
    return d + timedelta(days=rec.period_days)


@dataclass
class Recurring:
    merchant: str
    description: str
    category: str
    period: str
    period_days: int
    months: int  # 0 = semanal
    day_of_month: int
    amount: float  # valor típico (positivo)
    occurrences: int
    last_date: date
    next_date: date
    monthly_cost: float
    active: bool  # ainda aparece (não foi cancelada)


def detect(conn: sqlite3.Connection, today: date | None = None, min_occurrences: int = 3) -> list[Recurring]:
    today = today or date.today()
    rows = conn.execute(
        "SELECT merchant, description, category, date, amount FROM transactions "
        "WHERE amount < 0 AND merchant != '' ORDER BY merchant, date"
    ).fetchall()

    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        if r["category"] in NON_SPENDING or r["category"] == "Transferências":
            continue
        groups.setdefault(r["merchant"], []).append(r)

    found: list[Recurring] = []
    for merchant, items in groups.items():
        rec = _analyse(merchant, items, today, min_occurrences)
        if rec:
            found.append(rec)
    found.sort(key=lambda r: (not r.active, -r.monthly_cost))
    return found


def _analyse(merchant: str, items: list, today: date, min_occurrences: int) -> Recurring | None:
    # Junta movimentos do mesmo dia (ex.: pagamento dividido)
    by_day: dict[date, float] = {}
    for r in items:
        d = date.fromisoformat(r["date"])
        by_day[d] = by_day.get(d, 0.0) + abs(r["amount"])
    days = sorted(by_day)
    if len(days) < min_occurrences:
        return None

    intervals = [(b - a).days for a, b in zip(days, days[1:])]
    med = median(intervals)
    period = next((p for p in PERIODS if p[1] <= med <= p[2]), None)
    if period is None:
        return None
    name, lo, hi, months, per_month = period

    # A maioria dos intervalos tem de encaixar no período (tolera 1 falha)
    regular = sum(1 for i in intervals if lo * 0.8 <= i <= hi * 1.2)
    if regular < max(2, len(intervals) - 1):
        return None

    # O valor tem de ser estável: as últimas ocorrências não variam mais de 25% da mediana
    recent = [by_day[d] for d in days[-6:]]
    amount = median(recent)
    if amount < 1 or any(abs(a - amount) > 0.25 * amount + 1 for a in recent[-3:]):
        return None

    # Para semanais exige mais ocorrências (cafés diários não são "subscrições")
    if name == "Semanal" and len(days) < 6:
        return None

    last = days[-1]
    period_days = round(med)
    # Dia do mês habitual (o mais comum nas últimas ocorrências)
    recent_days = [d.day for d in days[-4:]]
    day_of_month = max(set(recent_days), key=recent_days.count)

    rec = Recurring(
        merchant=merchant,
        description=items[-1]["description"],
        category=items[-1]["category"],
        period=name,
        period_days=period_days,
        months=months,
        day_of_month=day_of_month,
        amount=round(amount, 2),
        occurrences=len(days),
        last_date=last,
        next_date=last,
        monthly_cost=round(amount * per_month, 2),
        # Se já passou mais de 1,5 períodos sem aparecer, provavelmente foi cancelada
        active=(today - last).days <= period_days * 1.5 + 5,
    )
    rec.next_date = step(last, rec)
    while rec.active and rec.next_date < today:
        rec.next_date = step(rec.next_date, rec)
    return rec


def month_forecast(recurring: list[Recurring], today: date | None = None) -> dict:
    """Despesas fixas do mês corrente: já pagas e ainda por pagar."""
    today = today or date.today()
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    paid, upcoming = [], []
    for r in recurring:
        if not r.active:
            continue
        if month_start <= r.last_date < next_month:
            paid.append(r)
        # Pode cair mais do que uma vez no mês (semanais)
        d = r.next_date
        while d < next_month:
            if d >= today:
                upcoming.append((d, r))
            d = step(d, r)
    upcoming.sort(key=lambda x: x[0])
    return {
        "paid": paid,
        "paid_total": round(sum(r.amount for r in paid), 2),
        "upcoming": upcoming,
        "upcoming_total": round(sum(r.amount for _, r in upcoming), 2),
        "monthly_fixed": round(sum(r.monthly_cost for r in recurring if r.active), 2),
    }
