"""Aplicação web (FastAPI + templates). Arranque: python -m fintrack"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import analytics, db, recurring
from ..categories import CATEGORIES
from ..importers import UnknownFormat, parse_file

HERE = Path(__file__).resolve().parent
MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
SOURCE_LABELS = {
    "revolut": "Revolut",
    "bank": "Banco",
    "degiro_portfolio": "DEGIRO — portfólio",
    "degiro_transactions": "DEGIRO — transações",
}


def eur(value) -> str:
    if value is None:
        return "—"
    s = f"{abs(value):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if value < 0 else ''}{s} €"


def month_label(m: str) -> str:
    y, mm = m.split("-")
    return f"{MONTHS_PT[int(mm) - 1]} {y[2:]}"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="FinTrack")
    conn = db.connect(db_path)
    app.state.conn = conn

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["eur"] = eur
    templates.env.filters["month"] = month_label
    templates.env.globals["source_label"] = lambda s: SOURCE_LABELS.get(s, s)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def render(request: Request, name: str, **ctx):
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        today = date.today()
        has_data = conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None
        has_portfolio = conn.execute("SELECT 1 FROM positions LIMIT 1").fetchone() is not None
        if not has_data and not has_portfolio:
            return render(request, "empty.html", active="dashboard")

        months = analytics.last_months(6, today)
        month_start = today.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        prev_start = (month_start - timedelta(days=1)).replace(day=1)

        totals = analytics.monthly_totals(conn, months)
        current = totals[-1]
        previous = totals[-2]
        by_cat = analytics.spending_by_category(conn, month_start.isoformat(), next_month.isoformat())
        by_cat_prev = {c["category"]: c["total"] for c in analytics.spending_by_category(
            conn, prev_start.isoformat(), month_start.isoformat())}
        matrix = analytics.category_matrix(conn, months)
        recs = recurring.detect(conn, today)
        forecast = recurring.month_forecast(recs, today)

        # Top 7 categorias dos últimos 6 meses; o resto fica em "Restantes".
        # A mesma categoria tem sempre a mesma cor nos dois gráficos (índice em chart_cats).
        cat_totals = sorted(matrix.items(), key=lambda kv: -sum(kv[1].values()))
        chart_cats = [c for c, _ in cat_totals[:7]]
        stacked = [{"label": c, "data": [matrix[c].get(m, 0) for m in months]} for c in chart_cats]
        others = [
            round(sum(v.get(m, 0) for c, v in matrix.items() if c not in chart_cats), 2) for m in months
        ]
        if any(others):
            stacked.append({"label": "Restantes", "data": others})

        donut = {c["category"]: c["total"] for c in by_cat if c["category"] in chart_cats}
        rest = round(sum(c["total"] for c in by_cat if c["category"] not in chart_cats), 2)
        donut_labels = [c for c in chart_cats if c in donut] + (["Restantes"] if rest else [])

        chart = {
            "labels": [month_label(m) for m in months],
            "spent": [t["spent"] for t in totals],
            "income": [t["income"] for t in totals],
            "order": chart_cats + ["Restantes"],
            "stacked": stacked,
            "donut": {"labels": donut_labels, "data": [donut.get(c, rest) for c in donut_labels]},
        }

        return render(
            request,
            "dashboard.html",
            active="dashboard",
            month=month_label(months[-1]),
            current=current,
            previous=previous,
            by_cat=by_cat,
            by_cat_prev=by_cat_prev,
            merchants=analytics.top_merchants(conn, month_start.isoformat(), next_month.isoformat(), 8),
            alerts=analytics.alerts(conn, today, {r.merchant for r in recs if r.active}),
            forecast=forecast,
            networth=analytics.net_worth(conn),
            chart_json=json.dumps(chart),
            last_txn=conn.execute("SELECT MAX(date) AS d FROM transactions").fetchone()["d"],
        )

    @app.get("/transacoes", response_class=HTMLResponse)
    def transactions(request: Request, q: str = "", categoria: str = "", conta: str = "", mes: str = "", page: int = 1):
        where, params = ["1=1"], []
        if q:
            where.append("description LIKE ?")
            params.append(f"%{q}%")
        if categoria:
            where.append("category = ?")
            params.append(categoria)
        if conta:
            where.append("account = ?")
            params.append(conta)
        if mes:
            where.append("substr(date, 1, 7) = ?")
            params.append(mes)
        sql_where = " AND ".join(where)
        per_page = 100
        total = conn.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS s FROM transactions WHERE {sql_where}", params).fetchone()
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE {sql_where} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
            (*params, per_page, (page - 1) * per_page),
        ).fetchall()
        accounts = [r["account"] for r in conn.execute("SELECT DISTINCT account FROM transactions ORDER BY account")]
        months = [r["m"] for r in conn.execute("SELECT DISTINCT substr(date,1,7) AS m FROM transactions ORDER BY m DESC")]
        return render(
            request, "transactions.html", active="transactions",
            rows=rows, total=total["n"], sum=total["s"], page=page, pages=max(1, -(-total["n"] // per_page)),
            q=q, categoria=categoria, conta=conta, mes=mes,
            categories=CATEGORIES, accounts=accounts, months=months,
        )

    @app.post("/transacoes/{txn_id}/categoria")
    def change_category(txn_id: int, request: Request, category: str = Form(...), remember: str = Form("")):
        changed = db.set_category(conn, txn_id, category, remember=bool(remember))
        back = urlsplit(request.headers.get("referer") or "/transacoes")
        query = [(k, v) for k, v in parse_qsl(back.query) if k != "alterados"] + [("alterados", str(changed))]
        return RedirectResponse(f"{back.path}?{urlencode(query)}#t{txn_id}", status_code=303)

    @app.get("/recorrentes", response_class=HTMLResponse)
    def recurring_page(request: Request):
        today = date.today()
        recs = recurring.detect(conn, today)
        return render(
            request, "recurring.html", active="recurring",
            active_recs=[r for r in recs if r.active],
            inactive_recs=[r for r in recs if not r.active],
            forecast=recurring.month_forecast(recs, today),
        )

    @app.get("/portfolio", response_class=HTMLResponse)
    def portfolio_page(request: Request):
        pf = analytics.portfolio(conn)
        chart = {
            "labels": [p["name"] for p in pf["positions"] if p["isin"] != "CASH"],
            "data": [p["value_eur"] for p in pf["positions"] if p["isin"] != "CASH"],
        }
        return render(request, "portfolio.html", active="portfolio", pf=pf,
                      networth=analytics.net_worth(conn), chart_json=json.dumps(chart))

    @app.get("/importar", response_class=HTMLResponse)
    def import_page(request: Request):
        history = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()
        return render(request, "import.html", active="import", history=history, results=None)

    @app.post("/importar", response_class=HTMLResponse)
    async def import_upload(request: Request, files: list[UploadFile] = File(...), bank_name: str = Form("ActivoBank")):
        results = []
        for f in files:
            content = await f.read()
            if not content:
                continue
            try:
                parsed = parse_file(content, f.filename or "", bank_name.strip() or "ActivoBank")
                summary = db.save_import(conn, parsed, f.filename or "")
                results.append({"file": f.filename, "ok": True, **summary, "warnings": parsed.warnings})
            except UnknownFormat as e:
                results.append({"file": f.filename, "ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001 — mostrar o erro ao utilizador em vez de rebentar
                results.append({"file": f.filename, "ok": False, "error": f"Erro a ler o ficheiro: {e}"})
        history = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()
        return render(request, "import.html", active="import", history=history, results=results)

    return app
