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


def _auth_config(application_doc: Any) -> tuple[dict[str, str], HTTPBasicAuth | None]:
    auth_mode = str(application_doc.settings.get("jira.auth_mode", "cloud")).strip().lower()
    username = str(application_doc.settings.get("jira.username", "")).strip()
    token = str(application_doc.settings.get("jira.token", "")).strip()

    headers = {"Accept": "application/json"}

    if auth_mode == "cloud":
        if not username or not token:
            raise ValueError("Cloud mode requires jira.username and jira.token")
        return headers, HTTPBasicAuth(username, token)

    if not token:
        raise ValueError("Standalone mode requires jira.token")

    if username:
        return headers, HTTPBasicAuth(username, token)

    headers["Authorization"] = f"Bearer {token}"
    return headers, None


def _fetch_projects(
    base_url: str,
    headers: dict[str, str],
    auth: HTTPBasicAuth | None,
    verify_ssl: bool,
) -> list[dict[str, Any]]:
    cloud_endpoint = "/rest/api/3/project/search"
    server_endpoint = "/rest/api/2/project"

    response = requests.get(
        f"{base_url}{cloud_endpoint}",
        headers=headers,
        auth=auth,
        params={"startAt": 0, "maxResults": 100},
        timeout=30,
        verify=verify_ssl,
    )

    if response.status_code == 200:
        payload = response.json()
        values = payload.get("values", [])
        if not isinstance(values, list):
            raise RuntimeError("Unexpected Jira response format for projects")
        projects = [project for project in values if isinstance(project, dict)]
        start_at = len(projects)
        total = int(payload.get("total", len(projects)))

        while start_at < total:
            page = requests.get(
                f"{base_url}{cloud_endpoint}",
                headers=headers,
                auth=auth,
                params={"startAt": start_at, "maxResults": 100},
                timeout=30,
                verify=verify_ssl,
            )
            if page.status_code != 200:
                raise RuntimeError(
                    f"Failed to retrieve Jira projects page: {page.status_code} {page.text}"
                )
            page_payload = page.json()
            page_values = page_payload.get("values", [])
            if not isinstance(page_values, list) or len(page_values) == 0:
                break
            projects.extend(project for project in page_values if isinstance(project, dict))
            start_at += len(page_values)
            print(f"Retrieved {len(projects)} projects so far...")

        return projects

    if response.status_code in {404, 405}:
        fallback = requests.get(
            f"{base_url}{server_endpoint}",
            headers=headers,
            auth=auth,
            timeout=30,
            verify=verify_ssl,
        )
        if fallback.status_code != 200:
            raise RuntimeError(f"Failed to retrieve Jira projects: {fallback.status_code} {fallback.text}")
        payload = fallback.json()
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Jira response format for projects")
        return [project for project in payload if isinstance(project, dict)]

    raise RuntimeError(f"Failed to retrieve Jira projects: {response.status_code} {response.text}")


def get_docs(application_doc: Any) -> list[dict[str, Any]] | dop.DopError:
    base_url = str(application_doc.settings.get("jira.url", "")).rstrip("/")
    token = str(application_doc.settings.get("jira.token", "")).strip()
    verify_ssl = _to_bool(application_doc.settings.get("jira.verify_ssl", True), default=True)

    if not base_url:
        return dop.error("Please specify Jira URL in Settings.")
    if not token:
        return dop.error("Please specify Jira token in Settings.")

    try:
        headers, auth = _auth_config(application_doc)
        projects = _fetch_projects(base_url, headers, auth, verify_ssl)
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    docs: list[dict[str, Any]] = []
    for project in projects:
        key = str(project.get("key", "")).strip()
        name = str(project.get("name", "")).strip()
        project_id = project.get("id")
        lead = project.get("lead", {})
        if not isinstance(lead, dict):
            lead = {}

        docs.append(
            {
                "id": project_id,
                "key": key,
                "name": name or key,
                "url": f"{base_url}/browse/{key}" if key else None,
                "description": project.get("description"),
                "project_type": project.get("projectTypeKey"),
                "style": project.get("style"),
                "is_simplified": project.get("simplified"),
                "lead": lead.get("displayName"),
                "lead_account_id": lead.get("accountId"),
                "archived": project.get("archived"),
            }
        )

    print(f"Found {len(docs)} projects.")
    return docs
