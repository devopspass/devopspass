from __future__ import annotations

from typing import Any

import requests
from requests.auth import HTTPBasicAuth

import dop


def _normalize_server_url(value: str, default: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        trimmed = default
    if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
        trimmed = f"https://{trimmed}"
    return trimmed.rstrip("/")


def _normalize_variant(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"cloud", "server"} else "cloud"


def _normalize_api_url(server: str, api_url: str, variant: str) -> str:
    explicit = api_url.strip()
    if explicit:
        if not explicit.startswith("http://") and not explicit.startswith("https://"):
            explicit = f"https://{explicit}"
        return explicit.rstrip("/")

    if variant == "cloud":
        return "https://api.bitbucket.org/2.0"
    return f"{server}/rest/api/latest"


def _build_auth(dop_app: Any, variant: str) -> tuple[dict[str, str], HTTPBasicAuth | None]:
    username = str(dop_app.settings.get("bitbucket.username", "")).strip()
    app_password = str(dop_app.settings.get("bitbucket.app_password", "")).strip()
    token = str(dop_app.settings.get("bitbucket.token", "")).strip()

    if variant == "cloud":
        # Atlassian API tokens (ATATT...) and App Passwords both use HTTP Basic auth:
        # username = Bitbucket username or Atlassian email, password = token or app_password
        cloud_secret = token or app_password
        if not username or not cloud_secret:
            raise ValueError(
                "For Bitbucket Cloud, set bitbucket.username to your Atlassian email "
                "and bitbucket.token to your Atlassian API token (or use bitbucket.app_password)."
            )
        return {"Accept": "application/json"}, HTTPBasicAuth(username, cloud_secret)

    # Server: PAT as Bearer, or Basic auth
    if token:
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}, None
    if username and app_password:
        return {"Accept": "application/json"}, HTTPBasicAuth(username, app_password)

    raise ValueError("For Bitbucket Server, provide bitbucket.token (personal access token) or bitbucket.username + bitbucket.app_password")


def do_test(dop_app: Any) -> dict[str, Any] | dop.DopError:
    variant = _normalize_variant(str(dop_app.settings.get("bitbucket.variant", "cloud")))
    server = _normalize_server_url(str(dop_app.settings.get("bitbucket.server", "https://bitbucket.org")), "https://bitbucket.org")
    api_url = _normalize_api_url(server, str(dop_app.settings.get("bitbucket.api_url", "")), variant)

    try:
        headers, auth = _build_auth(dop_app, variant)
    except ValueError as error:
        return dop.error(str(error))

    workspace = str(dop_app.settings.get("bitbucket.workspace", "")).strip()

    if variant == "cloud" and not workspace:
        return dop.error(
            "Please set bitbucket.workspace to your Bitbucket Cloud workspace slug "
            "(the part after bitbucket.org/ in your workspace URL, e.g. 'my-team')."
        )

    try:
        if variant == "cloud":
            response = requests.get(
                f"{api_url}/repositories/{workspace}",
                headers=headers, auth=auth, params={"pagelen": 1}, timeout=15,
            )
        else:
            response = requests.get(f"{api_url}/repos", headers=headers, auth=auth, params={"limit": 1}, timeout=15)
    except Exception as error:  # noqa: BLE001
        return dop.error(f"Failed to connect to Bitbucket: {error}")

    if response.status_code != 200:
        return dop.error(f"Bitbucket auth failed ({response.status_code}): {response.text[:300]}")

    if variant == "cloud":
        return {"status": "success", "message": f"Authenticated to Bitbucket Cloud, workspace: {workspace}"}

    configured_user = str(dop_app.settings.get("bitbucket.username", "")).strip() or "configured account"
    return {"status": "success", "message": f"Authenticated to Bitbucket Server as {configured_user}"}
