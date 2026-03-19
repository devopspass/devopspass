from __future__ import annotations

import os
import time
from typing import Any

import jwt
import requests


class FirebaseAuthVerifier:
    CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id.strip()
        self._issuer = f"https://securetoken.google.com/{self.project_id}"
        self._certs_cache: dict[str, str] = {}
        self._certs_expires_at = 0.0

    def verify_token(self, token: str) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        key_id = str(header.get("kid") or "").strip()
        if not key_id:
            raise ValueError("Token is missing key id")

        cert = self._get_public_cert(key_id)
        claims = jwt.decode(
            token,
            cert,
            algorithms=["RS256"],
            audience=self.project_id,
            issuer=self._issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
        if not str(claims.get("sub") or "").strip():
            raise ValueError("Token subject is missing")
        return claims

    def _get_public_cert(self, key_id: str) -> str:
        now = time.time()
        if now >= self._certs_expires_at or key_id not in self._certs_cache:
            self._refresh_certs()

        cert = self._certs_cache.get(key_id)
        if not cert:
            self._refresh_certs(force=True)
            cert = self._certs_cache.get(key_id)
            if not cert:
                raise ValueError("Token certificate was not found")
        return cert

    def _refresh_certs(self, force: bool = False) -> None:
        now = time.time()
        if not force and now < self._certs_expires_at and self._certs_cache:
            return

        response = requests.get(self.CERTS_URL, timeout=10)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not body:
            raise ValueError("Unexpected Firebase certs response")

        cache_control = response.headers.get("cache-control", "")
        max_age = 0
        for part in cache_control.split(","):
            candidate = part.strip().lower()
            if candidate.startswith("max-age="):
                try:
                    max_age = int(candidate.split("=", maxsplit=1)[1])
                except ValueError:
                    max_age = 0
                break

        self._certs_cache = {str(key): str(value) for key, value in body.items()}
        self._certs_expires_at = now + max(max_age, 300)


def is_auth_enabled() -> bool:
    value = os.environ.get('DOP_FIREBASE_AUTH_ENABLED', 'true').strip().lower()
    return value not in {'0', 'false', 'no'}
