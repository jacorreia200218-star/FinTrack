from datetime import date

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fintrack import db
from fintrack.bank import sync as bank_sync
from fintrack.bank.enable import Client, EnableBankingError, make_jwt


@pytest.fixture
def rsa_key(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    return path, key.public_key()


def test_jwt_has_required_header_and_claims(rsa_key):
    path, pub = rsa_key
    token = make_jwt("app-123", str(path))
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256" and header["kid"] == "app-123" and header["typ"] == "JWT"
    claims = jwt.decode(token, pub, algorithms=["RS256"], audience="api.enablebanking.com")
    assert claims["iss"] == "enablebanking.com"
    assert claims["exp"] - claims["iat"] <= 86400  # limite da API


class FakeApi:
    """Imita a API: uma conta com duas páginas de movimentos."""

    def __init__(self):
        self.calls = []

    def start_auth(self, aspsp, country, redirect_url, state, valid_days=90):
        self.calls.append(("auth", aspsp, country, state))
        return {"url": "https://banco.example/consent", "authorization_id": "a1"}

    def create_session(self, code):
        return {"session_id": "s1", "accounts": [{"uid": "acc-1"}],
                "access": {"valid_until": "2027-02-01T00:00:00+00:00"}}

    def transactions(self, uid, date_from, continuation_key=None):
        self.calls.append(("tx", uid, date_from, continuation_key))
        if continuation_key is None:
            return {"transactions": [
                {"status": "BOOK", "credit_debit_indicator": "DBIT", "booking_date": "2026-11-02",
                 "transaction_amount": {"amount": "12.50", "currency": "EUR"},
                 "remittance_information": ["Pingo Doce"], "creditor": {"name": "Pingo Doce"}},
                {"status": "PDNG", "credit_debit_indicator": "DBIT", "booking_date": "2026-11-03",
                 "transaction_amount": {"amount": "99.00", "currency": "EUR"}},
            ], "continuation_key": "pg2"}
        return {"transactions": [
            {"status": "BOOK", "credit_debit_indicator": "CRDT", "booking_date": "2026-11-04",
             "transaction_amount": {"amount": "300.00", "currency": "EUR"},
             "debtor": {"name": "Empresa"}},
        ], "continuation_key": None}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    bank_sync.ensure_schema(c)
    yield c
    c.close()


def test_full_link_and_sync(conn):
    api = FakeApi()
    url = bank_sync.begin_link(conn, api, "revolut", "https://fintrack.example/callback")
    assert url.startswith("https://banco.example")
    state = conn.execute("SELECT state FROM bank_links").fetchone()[0]

    assert bank_sync.finish_link(conn, api, state, "code-1") == "revolut"
    link = conn.execute("SELECT * FROM bank_links WHERE status = 'linked'").fetchone()
    assert link["session_id"] == "s1"

    added = bank_sync.sync_link(conn, api, link)
    assert added == 2  # o pendente fica de fora
    rows = conn.execute("SELECT account, date, amount, description FROM transactions ORDER BY date").fetchall()
    assert [tuple(r) for r in rows] == [
        ("Revolut", "2026-11-02", -12.5, "Pingo Doce"),
        ("Revolut", "2026-11-04", 300.0, "Empresa"),
    ]

    # Segunda sincronização não duplica
    link = conn.execute("SELECT * FROM bank_links WHERE status = 'linked'").fetchone()
    assert bank_sync.sync_link(conn, api, link) == 0


def test_state_cannot_be_reused(conn):
    api = FakeApi()
    bank_sync.begin_link(conn, api, "activobank", "https://x/callback")
    state = conn.execute("SELECT state FROM bank_links").fetchone()[0]
    bank_sync.finish_link(conn, api, state, "c")
    with pytest.raises(EnableBankingError):
        bank_sync.finish_link(conn, api, state, "c")


def test_expired_consent_marks_link(conn):
    class Expired(FakeApi):
        def transactions(self, *a, **k):
            raise EnableBankingError("401 em /accounts: expired")

    api = FakeApi()
    bank_sync.begin_link(conn, api, "revolut", "https://x/callback")
    state = conn.execute("SELECT state FROM bank_links").fetchone()[0]
    bank_sync.finish_link(conn, api, state, "c")
    results = bank_sync.sync_all(conn, Expired())
    assert results[0]["ok"] is False
    assert conn.execute("SELECT status FROM bank_links").fetchone()[0] == "expired"
