import pytest
from fastapi.testclient import TestClient

from fintrack.auth import COOKIE, MAX_FAILURES, Auth
from fintrack.web.app import create_app

PASSWORD = "uma-password-boa"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "t.db", require_login=True))


def test_without_password_redirects_to_setup(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/criar-password"
    assert client.get("/static/style.css").status_code == 200  # estáticos são públicos


def test_setup_then_login_flow(client):
    r = client.post("/criar-password", data={"password": "curta", "confirm": "curta"})
    assert "pelo menos" in r.text
    r = client.post("/criar-password", data={"password": PASSWORD, "confirm": "outra-coisa-xx"})
    assert "não são iguais" in r.text

    r = client.post("/criar-password", data={"password": PASSWORD, "confirm": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and COOKIE in r.cookies
    assert client.get("/").status_code == 200  # já tem sessão

    # Não se pode redefinir a password depois de criada
    r = client.post("/criar-password", data={"password": "x" * 20, "confirm": "x" * 20}, follow_redirects=False)
    assert r.headers["location"] == "/login"

    client.post("/sair")
    client.cookies.clear()
    assert client.get("/importar", follow_redirects=False).headers["location"] == "/login"
    assert "errada" in client.post("/login", data={"password": "nope"}).text
    r = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/importar").status_code == 200


def test_forged_cookie_rejected(client):
    client.post("/criar-password", data={"password": PASSWORD, "confirm": PASSWORD})
    client.cookies.clear()
    client.cookies.set(COOKIE, "9999999999.assinatura-falsa")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_lockout_after_failures(tmp_path):
    a = Auth(tmp_path / "auth.json")
    a.set_password(PASSWORD)
    for _ in range(MAX_FAILURES):
        assert not a.check_password("errada")
    assert a.locked_for() > 0
    assert not a.check_password(PASSWORD)  # bloqueado mesmo com a certa


def test_password_change_invalidates_sessions(tmp_path):
    a = Auth(tmp_path / "auth.json")
    a.set_password(PASSWORD)
    token = a.make_token()
    assert a.valid_token(token)
    assert Auth(tmp_path / "auth.json").valid_token(token)  # sobrevive a reinícios
    a.set_password("outra-password-boa")
    assert not a.valid_token(token)


def test_no_api_docs_exposed(client):
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path, follow_redirects=False).status_code in (303, 404)
