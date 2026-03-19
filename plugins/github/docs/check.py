from __future__ import annotations

from typing import Any

import requests

import dop


def _normalize_server_url(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
        trimmed = f"https://{trimmed}"
    return trimmed.rstrip("/")


def _normalize_api_url(server: str, api_url: str) -> str:
    explicit = _normalize_server_url(api_url)
    if explicit:
        return explicit

    normalized_server = _normalize_server_url(server)
    if not normalized_server:
        return ""

    if normalized_server == "https://github.com":
        return "https://api.github.com"

    return f"{normalized_server}/api/v3"


def do_test(dop_app: Any) -> dict[str, Any] | dop.DopError:
    server = _normalize_server_url(str(dop_app.settings.get("github.server", "https://github.com")))
    api_url = _normalize_api_url(server, str(dop_app.settings.get("github.api_url", "https://api.github.com")))
    token = str(dop_app.settings.get("github.token", "")).strip()

    if not token:
        return dop.error("github.token is required")
    if not api_url:
        return dop.error("github.api_url (or github.server) is required")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.get(f"{api_url}/user", headers=headers, timeout=15)
    except Exception as error:  # noqa: BLE001
        return dop.error(f"Failed to connect to GitHub: {error}")

    if response.status_code != 200:
        return dop.error(f"GitHub auth failed ({response.status_code}): {response.text[:300]}")

    payload = response.json() if response.content else {}
    username = str(payload.get("login") or payload.get("name") or "").strip() or "unknown"

    return {
        "status": "success",
        "message": f"Authenticated as {username}",
    }
