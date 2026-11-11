"""Aplicação web (FastAPI + templates). Arranque: python -m fintrack"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from .. import analytics, db, recurring
from ..auth import COOKIE, MIN_LENGTH, SESSION_DAYS, Auth
from ..bank import sync as bank_sync
from ..bank.enable import EnableBankingError, is_configured, load_config
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


PUBLIC_PATHS = ("/login", "/criar-password", "/static/")


def create_app(db_path: str | Path | None = None, require_login: bool | None = None) -> FastAPI:
    """require_login: obriga a ter palavra-passe (no servidor). Por omissão lê FINTRACK_REQUIRE_LOGIN.
    Se já existir uma palavra-passe definida, o login é sempre obrigatório."""
    app = FastAPI(title="FinTrack", docs_url=None, redoc_url=None, openapi_url=None)
    conn = db.connect(db_path)
    app.state.conn = conn
    auth = Auth(Path(db_path or db.DEFAULT_PATH).parent / "auth.json")
    app.state.auth = auth
    if require_login is None:
        require_login = os.environ.get("FINTRACK_REQUIRE_LOGIN") == "1"

    @app.middleware("http")
    async def login_required(request: Request, call_next):
        path = request.url.path
        if (auth.configured or require_login) and not path.startswith(PUBLIC_PATHS):
            if not auth.configured:
                return RedirectResponse("/criar-password", status_code=303)
            if not auth.valid_token(request.cookies.get(COOKIE)):
                return RedirectResponse("/login", status_code=303)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def start_session(request: Request) -> RedirectResponse:
        resp = RedirectResponse("/", status_code=303)
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(COOKIE, auth.make_token(), max_age=SESSION_DAYS * 86400,
                        httponly=True, secure=https, samesite="lax")
        return resp

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["eur"] = eur
    templates.env.filters["month"] = month_label
    templates.env.filters["fromjson"] = lambda v: json.loads(v or "[]")
    templates.env.globals["source_label"] = lambda s: SOURCE_LABELS.get(s, s)
    templates.env.globals["auth_enabled"] = lambda: auth.configured
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def render(request: Request, name: str, **ctx):
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if not auth.configured:
            return RedirectResponse("/criar-password", status_code=303)
        return render(request, "login.html", mode="login", error=None)

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, password: str = Form("")):
        if not auth.configured:
            return RedirectResponse("/criar-password", status_code=303)
        if auth.check_password(password):
            return start_session(request)
        wait = auth.locked_for()
        error = (f"Demasiadas tentativas. Tenta de novo daqui a {wait // 60 + 1} minutos."
                 if wait else "Palavra-passe errada.")
        return render(request, "login.html", mode="login", error=error)

    @app.get("/criar-password", response_class=HTMLResponse)
    def setup_page(request: Request):
        if auth.configured:
            return RedirectResponse("/login", status_code=303)
        return render(request, "login.html", mode="setup", error=None, min_length=MIN_LENGTH)

    @app.post("/criar-password", response_class=HTMLResponse)
    def setup(request: Request, password: str = Form(""), confirm: str = Form("")):
        if auth.configured:  # só se pode definir uma vez
            return RedirectResponse("/login", status_code=303)
        error = None
        if len(password) < MIN_LENGTH:
            error = f"A palavra-passe tem de ter pelo menos {MIN_LENGTH} caracteres."
        elif password != confirm:
            error = "As duas palavras-passe não são iguais."
        if error:
            return render(request, "login.html", mode="setup", error=error, min_length=MIN_LENGTH)
        auth.set_password(password)
        return start_session(request)

    @app.post("/sair")
    def logout():
        resp = RedirectResponse("/login" if auth.configured else "/", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

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

    @app.get("/copia-seguranca")
    def download_backup():
        """Cópia completa da base de dados (abre-se com qualquer leitor de SQLite)."""
        tmp = Path(tempfile.mkdtemp()) / f"fintrack-{date.today().isoformat()}.db"
        dest = sqlite3.connect(tmp)
        conn.backup(dest)
        dest.close()
        return FileResponse(tmp, filename=tmp.name, media_type="application/vnd.sqlite3",
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    bank_sync.ensure_schema(conn)

    def _redirect_url() -> str:
        return load_config().get("EB_REDIRECT_URL", "")

    @app.get("/banco", response_class=HTMLResponse)
    def bank_page(request: Request, msg: str = "", erro: str = ""):
        links = conn.execute("SELECT * FROM bank_links ORDER BY id DESC").fetchall()
        by_bank = {}
        for l in links:  # só a ligação mais recente de cada banco
            by_bank.setdefault(l["bank"], l)
        banks = []
        for key, (_, _, name) in bank_sync.BANKS.items():
            banks.append({"key": key, "name": name, "link": by_bank.get(key)})
        return render(request, "banco.html", active="bank", banks=banks,
                      configured=is_configured(), msg=msg, erro=erro)

    @app.post("/banco/ligar", response_class=HTMLResponse)
    def bank_connect(request: Request, bank: str = Form(...)):
        if not is_configured():
            return RedirectResponse("/banco?erro=O+Enable+Banking+ainda+n%C3%A3o+est%C3%A1+configurado+no+servidor", status_code=303)
        try:
            from ..bank.enable import Client
            url = bank_sync.begin_link(conn, Client(), bank, _redirect_url())
            return RedirectResponse(url, status_code=303)
        except (EnableBankingError, ValueError) as e:
            return RedirectResponse(f"/banco?erro={urlencode({'e': str(e)})[2:]}", status_code=303)

    @app.get("/callback")
    def bank_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/banco?erro=A+liga%C3%A7%C3%A3o+foi+cancelada+ou+recusada", status_code=303)
        try:
            from ..bank.enable import Client
            bank = bank_sync.finish_link(conn, Client(), state, code)
            added = bank_sync.sync_link(conn, Client(), conn.execute(
                "SELECT * FROM bank_links WHERE bank = ? AND status = 'linked' ORDER BY id DESC LIMIT 1", (bank,)).fetchone())
            return RedirectResponse(f"/banco?msg=Liga%C3%A7%C3%A3o+feita.+{added}+movimentos+importados.", status_code=303)
        except EnableBankingError as e:
            return RedirectResponse(f"/banco?erro={urlencode({'e': str(e)})[2:]}", status_code=303)

    @app.post("/banco/sincronizar")
    def bank_sync_now():
        results = bank_sync.sync_all(conn)
        ok = sum(r.get("added", 0) for r in results if r["ok"])
        bad = [r for r in results if not r["ok"]]
        if bad:
            return RedirectResponse(f"/banco?erro={urlencode({'e': 'Algum banco precisa de ser ligado de novo.'})[2:]}", status_code=303)
        return RedirectResponse(f"/banco?msg=Sincronizado.+{ok}+movimentos+novos.", status_code=303)

    def _group_totals(groups_map: dict, balances: dict, assets: list, pf: dict) -> dict:
        totals = {g: 0.0 for g in analytics.GROUPS}
        for name, g in groups_map.items():
            if name in balances:
                totals[g] += balances[name]["balance"]
        for a in assets:
            totals[a["grupo"]] = totals.get(a["grupo"], 0.0) + a["last"]
        totals["Investimentos"] += pf["value"] + pf["cash"]
        return {g: round(v, 2) for g, v in totals.items()}

    @app.get("/patrimonio", response_class=HTMLResponse)
    def wealth(request: Request, tab: str = "Pessoal", msg: str = ""):
        if tab not in analytics.GROUPS:
            tab = "Pessoal"
        groups_map = analytics.groups_for_accounts(conn)
        balances = {b["account"]: b for b in analytics.account_balances(conn)}
        assets = analytics.valuation_summary(conn)
        pf = analytics.portfolio(conn)
        totals = _group_totals(groups_map, balances, assets, pf)
        accounts = [{"name": n, "balance": balances.get(n)} for n, g in groups_map.items() if g == tab]
        tab_assets = [a for a in assets if a["grupo"] == tab]
        btc_inv, btc_n = analytics.btc_invested(conn)
        chart = {
            "labels": sorted({h["date"] for a in tab_assets for h in a["history"]}),
            "series": [
                {"label": a["asset"], "points": {h["date"]: h["value"] for h in a["history"]}}
                for a in tab_assets
            ],
        }
        return render(
            request, "patrimonio.html", active="wealth",
            tabs=analytics.GROUPS, tab=tab, totals=totals, total=round(totals[tab], 2),
            accounts=accounts, assets=tab_assets, all_assets=assets,
            all_accounts=sorted(groups_map.items()), pf=pf,
            btc_inv=btc_inv, btc_n=btc_n, msg=msg, today=date.today().isoformat(),
            chart_json=json.dumps(chart),
        )

    @app.post("/patrimonio/grupo")
    def wealth_set_group(account: str = Form(...), grupo: str = Form(...), tab: str = Form("Pessoal")):
        if grupo not in analytics.GROUPS:
            return RedirectResponse("/patrimonio", status_code=303)
        conn.execute(
            "INSERT INTO account_groups (account, grupo) VALUES (?, ?) "
            "ON CONFLICT(account) DO UPDATE SET grupo = excluded.grupo",
            (account, grupo),
        )
        conn.commit()
        return RedirectResponse(f"/patrimonio?tab={urlencode({'t': grupo})[2:]}", status_code=303)

    @app.post("/patrimonio/valor")
    def wealth_add_value(
        asset: str = Form(...), grupo: str = Form(...), value: str = Form(...),
        day: str = Form(""), note: str = Form(""),
    ):
        try:
            clean = value.replace("€", "").replace(" ", "").strip()
            if "," in clean:  # formato português: 1.025,50
                clean = clean.replace(".", "").replace(",", ".")
            amount = float(clean)
            when = date.fromisoformat(day) if day else date.today()
        except ValueError:
            return RedirectResponse("/patrimonio?msg=Valor+ou+data+inv%C3%A1lidos", status_code=303)
        if grupo not in analytics.GROUPS or not asset.strip():
            return RedirectResponse("/patrimonio", status_code=303)
        conn.execute(
            "INSERT INTO valuations (asset, grupo, date, value, note) VALUES (?, ?, ?, ?, ?)",
            (asset.strip(), grupo, when.isoformat(), amount, note.strip()),
        )
        conn.commit()
        return RedirectResponse(f"/patrimonio?tab={urlencode({'t': grupo})[2:]}", status_code=303)

    @app.post("/patrimonio/valor/apagar")
    def wealth_delete_value(id: int = Form(...), grupo: str = Form("Pessoal")):
        conn.execute("DELETE FROM valuations WHERE id = ?", (id,))
        conn.commit()
        return RedirectResponse(f"/patrimonio?tab={urlencode({'t': grupo})[2:]}", status_code=303)

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
