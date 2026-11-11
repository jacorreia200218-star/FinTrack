from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fintrack.demo import generate
from fintrack.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "test.db")
    return TestClient(app)


def test_empty_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Importar extratos" in r.text


def test_full_flow_with_demo_data(client, tmp_path):
    paths = generate(tmp_path / "demo", today=date.today())
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in paths]
    r = client.post("/importar", files=files, data={"bank_name": "ActivoBank"})
    assert r.status_code == 200
    assert r.text.count("✓") == 4, r.text

    for page in ("/", "/transacoes", "/recorrentes", "/portfolio", "/importar",
                 "/transacoes?categoria=Supermercado&mes=" + date.today().strftime("%Y-%m")):
        resp = client.get(page)
        assert resp.status_code == 200, page

    dash = client.get("/").text
    assert "Gasto este mês" in dash and "Património" in dash
    assert "Netflix" in client.get("/recorrentes").text
    assert "IE00BK5BQT80" in client.get("/portfolio").text

    txn_id = client.app.state.conn.execute(
        "SELECT id FROM transactions WHERE description = 'Galp' LIMIT 1").fetchone()[0]
    r = client.post(f"/transacoes/{txn_id}/categoria", data={"category": "Transportes", "remember": "1"},
                    headers={"referer": "http://testserver/transacoes?q=Galp"}, follow_redirects=False)
    assert r.status_code == 303 and "q=Galp" in r.headers["location"]
    left = client.app.state.conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE description = 'Galp' AND category != 'Transportes'").fetchone()[0]
    assert left == 0


def test_bad_file_shows_error(client):
    r = client.post("/importar", files=[("files", ("x.csv", b"foo,bar\n1,2\n", "text/csv"))])
    assert r.status_code == 200
    assert "Não reconheci" in r.text


def test_backup_download(client, tmp_path):
    import sqlite3

    client.post("/importar", files=[("files", ("r.csv", (Path(__file__).parent / "fixtures" / "revolut_en.csv").read_bytes(), "text/csv"))])
    r = client.get("/copia-seguranca")
    assert r.status_code == 200
    out = tmp_path / "copia.db"
    out.write_bytes(r.content)
    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5
