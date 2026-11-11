"""Login com palavra-passe (um único utilizador).

A palavra-passe é guardada só como hash (PBKDF2-SHA256) em data/auth.json.
A sessão é um cookie assinado com HMAC; mudar a palavra-passe invalida as sessões antigas.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

ITERATIONS = 600_000
SESSION_DAYS = 30
COOKIE = "fintrack_session"
MIN_LENGTH = 10

# Proteção contra tentativas repetidas: 5 falhas -> bloqueio de 15 minutos
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


class Auth:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._failures: list[float] = []
        self._data = self._load()

    def _load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    @property
    def configured(self) -> bool:
        return self._data is not None

    def _hash(self, password: str, salt: bytes) -> str:
        return _b64(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS))

    def set_password(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        self._data = {
            "salt": _b64(salt),
            "hash": self._hash(password, salt),
            "secret": secrets.token_urlsafe(32),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # ---- tentativas ----
    def locked_for(self) -> int:
        """Segundos que faltam para desbloquear (0 se não estiver bloqueado)."""
        now = time.time()
        self._failures = [t for t in self._failures if now - t < LOCK_SECONDS]
        if len(self._failures) >= MAX_FAILURES:
            return int(LOCK_SECONDS - (now - self._failures[0])) + 1
        return 0

    def check_password(self, password: str) -> bool:
        if not self._data or self.locked_for():
            return False
        salt = base64.urlsafe_b64decode(self._data["salt"] + "==")
        ok = hmac.compare_digest(self._hash(password, salt), self._data["hash"])
        if ok:
            self._failures.clear()
        else:
            self._failures.append(time.time())
        return ok

    # ---- sessão ----
    def _sign(self, payload: str) -> str:
        key = (self._data["secret"] + self._data["hash"]).encode()
        return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())

    def make_token(self) -> str:
        expires = str(int(time.time()) + SESSION_DAYS * 86400)
        return f"{expires}.{self._sign(expires)}"

    def valid_token(self, token: str | None) -> bool:
        if not token or not self._data or "." not in token:
            return False
        expires, sig = token.split(".", 1)
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(sig, self._sign(expires))
