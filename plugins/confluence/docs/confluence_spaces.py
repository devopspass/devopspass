from __future__ import annotations

from typing import Any

import requests
from requests.auth import HTTPBasicAuth

import dop


def _to_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _auth_attempts(application_doc: Any) -> list[tuple[dict[str, str], HTTPBasicAuth | None]]:
    auth_mode = str(application_doc.settings.get("confluence.auth_mode", "cloud")).strip().lower()
    username = str(application_doc.settings.get("confluence.username", "")).strip()
    token = str(application_doc.settings.get("confluence.token", "")).strip()

    headers = {"Accept": "application/json"}

    if auth_mode == "cloud":
        if not username or not token:
            raise ValueError("Cloud mode requires confluence.username and confluence.token")
        return [(headers, HTTPBasicAuth(username, token))]

    if not token:
        raise ValueError("Standalone mode requires confluence.token")

    attempts: list[tuple[dict[str, str], HTTPBasicAuth | None]] = []
    bearer_headers = dict(headers)
    bearer_headers["Authorization"] = f"Bearer {token}"
    attempts.append((bearer_headers, None))

    if username:
        attempts.append((dict(headers), HTTPBasicAuth(username, token)))

    return attempts


def _candidate_bases(base_url: str, auth_mode: str) -> list[str]:
    normalized = base_url.rstrip("/")
    if auth_mode == "cloud":
        if normalized.endswith("/wiki"):
            return [normalized]
        return [f"{normalized}/wiki"]

    ret: list[str] = [normalized]
    if normalized.endswith("/wiki"):
        ret.append(normalized[: -len("/wiki")])
    else:
        ret.append(f"{normalized}/wiki")
    return [item for index, item in enumerate(ret) if item and item not in ret[:index]]


def _fetch_spaces(
    base_url: str,
    auth_mode: str,
    auth_attempts: list[tuple[dict[str, str], HTTPBasicAuth | None]],
    verify_ssl: bool,
) -> list[dict[str, Any]]:
    last_error = ""
    auth_error: str | None = None
    for api_base in _candidate_bases(base_url, auth_mode):
        for headers, auth in auth_attempts:
            start = 0
            limit = 100
            collected: list[dict[str, Any]] = []

            while True:
                response = requests.get(
                    f"{api_base}/rest/api/space",
                    headers=headers,
                    auth=auth,
                    params={
                        "start": start,
                        "limit": limit,
                        "expand": "description.plain,homepage",
                    },
                    timeout=30,
                    verify=verify_ssl,
                )

                if response.status_code == 401:
                    auth_error = (
                        "Confluence authentication failed (HTTP 401). Verify confluence.auth_mode, "
                        "confluence.url, and confluence.username + confluence.token pairing. "
                        f"Raw response: {response.text}"
                    )
                    last_error = f"{api_base} returned 401: {response.text}"
                    break

                if response.status_code == 403:
                    raise RuntimeError(
                        "Confluence authentication succeeded but access was denied (HTTP 403). "
                        "Verify that this user has Confluence product access and permissions "
                        f"for spaces. Raw response: {response.text}"
                    )

                if response.status_code in {404, 405, 410}:
                    last_error = f"{api_base} returned {response.status_code}: {response.text}"
                    break

                if response.status_code != 200:
                    raise RuntimeError(
                        f"Failed to retrieve Confluence spaces: {response.status_code} {response.text}"
                    )

                payload = response.json()
                results = payload.get("results", [])
                if not isinstance(results, list):
                    raise RuntimeError("Unexpected Confluence response format for spaces")

                collected.extend(item for item in results if isinstance(item, dict))
                print(f"Retrieved {len(collected)} spaces so far...")

                if len(results) < limit:
                    return collected
                start += len(results)

    if auth_error:
        raise RuntimeError(auth_error)

    raise RuntimeError(f"Failed to query Confluence spaces. Last error: {last_error}")


def get_docs(application_doc: Any) -> list[dict[str, Any]] | dop.DopError:
    base_url = str(application_doc.settings.get("confluence.url", "")).rstrip("/")
    token = str(application_doc.settings.get("confluence.token", "")).strip()
    auth_mode = str(application_doc.settings.get("confluence.auth_mode", "cloud")).strip().lower()
    verify_ssl = _to_bool(application_doc.settings.get("confluence.verify_ssl", True), default=True)

    if not base_url:
        return dop.error("Please specify Confluence URL in Settings.")
    if not token:
        return dop.error("Please specify Confluence token in Settings.")

    try:
        auth_attempts = _auth_attempts(application_doc)
        spaces = _fetch_spaces(base_url, auth_mode, auth_attempts, verify_ssl)
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    docs: list[dict[str, Any]] = []
    for space in spaces:
        key = str(space.get("key", "")).strip()
        name = str(space.get("name", "")).strip()

        description = space.get("description", {})
        if not isinstance(description, dict):
            description = {}
        description_plain = description.get("plain", {}) if isinstance(description, dict) else {}
        if not isinstance(description_plain, dict):
            description_plain = {}

        homepage = space.get("homepage", {})
        if not isinstance(homepage, dict):
            homepage = {}
        homepage_links = homepage.get("_links", {}) if isinstance(homepage, dict) else {}
        if not isinstance(homepage_links, dict):
            homepage_links = {}
        homepage_webui = str(homepage_links.get("webui", "")).strip()
        space_url = f"{base_url}{homepage_webui}" if homepage_webui.startswith("/") else None

        docs.append(
            {
                "id": space.get("id"),
                "key": key,
                "name": name or key,
                "url": space_url,
                "type": space.get("type"),
                "status": space.get("status"),
                "description": description_plain.get("value"),
            }
        )

    print(f"Found {len(docs)} spaces.")
    return docs
