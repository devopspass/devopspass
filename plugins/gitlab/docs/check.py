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


def do_test(dop_app: Any) -> dict[str, Any] | dop.DopError:
    server = _normalize_server_url(str(dop_app.settings.get("gitlab.server", "")))
    token = str(dop_app.settings.get("gitlab.token", "")).strip()

    if not server:
        return dop.error("gitlab.server is required")
    if not token:
        return dop.error("gitlab.token is required")

    try:
        response = requests.get(
            f"{server}/api/v4/user",
            headers={"PRIVATE-TOKEN": token},
            timeout=15,
        )
    except Exception as error:  # noqa: BLE001
        return dop.error(f"Failed to connect to GitLab: {error}")

    if response.status_code != 200:
        return dop.error(f"GitLab auth failed ({response.status_code}): {response.text[:300]}")

    payload = response.json() if response.content else {}
    username = str(payload.get("username") or payload.get("name") or "").strip()
    if not username:
        username = "unknown"

    return {"status": "success", "message": f"Authenticated as {username}"}
