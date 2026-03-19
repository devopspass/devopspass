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


def _search_pages(
    base_url: str,
    cql: str,
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
                    f"{api_base}/rest/api/content/search",
                    headers=headers,
                    auth=auth,
                    params={
                        "cql": cql,
                        "start": start,
                        "limit": limit,
                        "expand": "space,history.lastUpdated,version,body.storage",
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
                        f"for pages/spaces. Raw response: {response.text}"
                    )

                if response.status_code in {404, 405, 410}:
                    last_error = f"{api_base} returned {response.status_code}: {response.text}"
                    break

                if response.status_code != 200:
                    raise RuntimeError(
                        f"Failed to retrieve Confluence pages: {response.status_code} {response.text}"
                    )

                payload = response.json()
                results = payload.get("results", [])
                if not isinstance(results, list):
                    raise RuntimeError("Unexpected Confluence response format for pages")

                collected.extend(item for item in results if isinstance(item, dict))
                print(f"Retrieved {len(collected)} pages so far...")

                if len(results) < limit:
                    return collected
                start += len(results)

    if auth_error:
        raise RuntimeError(auth_error)

    raise RuntimeError(f"Failed to query Confluence pages. Last error: {last_error}")


def get_docs(application_doc: Any) -> list[dict[str, Any]] | dop.DopError:
    base_url = str(application_doc.settings.get("confluence.url", "")).rstrip("/")
    token = str(application_doc.settings.get("confluence.token", "")).strip()
    cql = str(
        application_doc.settings.get(
            "confluence.cql",
            "type = page ORDER BY lastmodified DESC",
        )
    ).strip()
    auth_mode = str(application_doc.settings.get("confluence.auth_mode", "cloud")).strip().lower()
    verify_ssl = _to_bool(application_doc.settings.get("confluence.verify_ssl", True), default=True)

    if not base_url:
        return dop.error("Please specify Confluence URL in Settings.")
    if not token:
        return dop.error("Please specify Confluence token in Settings.")
    if not cql:
        return dop.error("Please specify Confluence CQL in Settings.")

    try:
        auth_attempts = _auth_attempts(application_doc)
        pages = _search_pages(base_url, cql, auth_mode, auth_attempts, verify_ssl)
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    docs: list[dict[str, Any]] = []
    for page in pages:
        page_id = str(page.get("id", "")).strip()
        title = str(page.get("title", "")).strip()

        space = page.get("space", {})
        if not isinstance(space, dict):
            space = {}

        history = page.get("history", {})
        if not isinstance(history, dict):
            history = {}
        last_updated = history.get("lastUpdated", {}) if isinstance(history, dict) else {}
        if not isinstance(last_updated, dict):
            last_updated = {}

        version = page.get("version", {})
        if not isinstance(version, dict):
            version = {}

        body = page.get("body", {})
        if not isinstance(body, dict):
            body = {}
        body_storage = body.get("storage", {}) if isinstance(body, dict) else {}
        if not isinstance(body_storage, dict):
            body_storage = {}

        links = page.get("_links", {})
        if not isinstance(links, dict):
            links = {}
        webui = str(links.get("webui", "")).strip()
        page_url = f"{base_url}{webui}" if webui.startswith("/") else None

        docs.append(
            {
                "id": page_id,
                "title": title or page_id,
                "name": title or page_id,
                "url": page_url,
                "space_key": space.get("key"),
                "space_name": space.get("name"),
                "status": page.get("status"),
                "version": version,
                "last_updated": last_updated,
                "history": history,
                "body_storage": body_storage,
            }
        )

    print(f"Found {len(docs)} pages.")
    return docs
