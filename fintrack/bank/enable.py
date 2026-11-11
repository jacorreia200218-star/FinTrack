"""Cliente mínimo da API do Enable Banking (contas pessoais, só leitura).

Credenciais: o ID da aplicação e a chave privada ficam FORA do código, no servidor, em
/var/lib/fintrack/secrets/ (ver docs/INSTALAR.md). Nunca vão para o git.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

API = "https://api.enablebanking.com"
SECRETS_DIR = Path(os.environ.get("FINTRACK_SECRETS", "/var/lib/fintrack/secrets"))
DEFAULT_ENV = SECRETS_DIR / "enablebanking.env"
DEFAULT_KEY = SECRETS_DIR / "enablebanking.pem"


class EnableBankingError(Exception):
    pass


def load_config() -> dict:
    """Lê o ficheiro de configuração do servidor (EB_APP_ID, EB_REDIRECT_URL...).
    Variáveis de ambiente têm prioridade, para testes locais."""
    cfg: dict[str, str] = {}
    env_file = Path(os.environ.get("EB_ENV_FILE", DEFAULT_ENV))
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    for k in ("EB_APP_ID", "EB_REDIRECT_URL"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    cfg["EB_KEY_PATH"] = os.environ.get("EB_KEY_PATH", str(DEFAULT_KEY))
    return cfg


def is_configured(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("EB_APP_ID") and cfg.get("EB_REDIRECT_URL") and Path(cfg["EB_KEY_PATH"]).exists())


def make_jwt(app_id: str, key_path: str, now: float | None = None) -> str:
    """JWT assinado com a chave privada, válido 1 hora (o máximo aceite é 24 horas)."""
    issued = int(now or time.time())
    payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": issued, "exp": issued + 3600}
    private_key = Path(key_path).read_text()
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": app_id, "typ": "JWT"})


class Client:
    def __init__(self, cfg: dict | None = None, transport: httpx.BaseTransport | None = None):
        cfg = cfg or load_config()
        self.app_id = cfg["EB_APP_ID"]
        self.key_path = cfg["EB_KEY_PATH"]
        self.http = httpx.Client(base_url=API, timeout=30, transport=transport)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {make_jwt(self.app_id, self.key_path)}"}
        r = self.http.request(method, path, headers=headers, **kwargs)
        if r.status_code >= 400:
            raise EnableBankingError(f"{r.status_code} em {path}: {r.text[:300]}")
        return r.json()

    def start_auth(self, aspsp: str, country: str, redirect_url: str, state: str, valid_days: int = 90) -> dict:
        """Devolve {'url': ..., 'authorization_id': ...}. O utilizador é enviado para 'url'."""
        valid_until = datetime.now(timezone.utc) + timedelta(days=valid_days)
        body = {
            "access": {"valid_until": valid_until.isoformat(), "balances": True, "transactions": True},
            "aspsp": {"name": aspsp, "country": country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": "personal",
            "language": "pt",
        }
        return self._request("POST", "/auth", json=body)

    def create_session(self, code: str) -> dict:
        """Troca o código devolvido pelo banco por uma sessão com acesso às contas."""
        return self._request("POST", "/sessions", json={"code": code})

    def transactions(self, account_uid: str, date_from: str, continuation_key: str | None = None) -> dict:
        params = {"date_from": date_from}
        if continuation_key:
            params["continuation_key"] = continuation_key
        return self._request("GET", f"/accounts/{account_uid}/transactions", params=params)
