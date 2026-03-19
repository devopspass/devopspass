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


def _search_issues(
    base_url: str,
    jql: str,
    headers: dict[str, str],
    auth: HTTPBasicAuth | None,
    verify_ssl: bool,
) -> list[dict[str, Any]]:
    fields = [
        "summary",
        "status",
        "priority",
        "issuetype",
        "project",
        "assignee",
        "reporter",
        "created",
        "updated",
        "description",
        "comment",
        "worklog",
    ]

    endpoints = ["/rest/api/3/search/jql", "/rest/api/3/search", "/rest/api/2/search"]
    last_error = ""

    for endpoint in endpoints:
        start_at = 0
        page_size = 100
        collected: list[dict[str, Any]] = []

        while True:
            response = requests.get(
                f"{base_url}{endpoint}",
                headers=headers,
                auth=auth,
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": page_size,
                    "fields": ",".join(fields),
                },
                timeout=30,
                verify=verify_ssl,
            )

            if response.status_code in {404, 405, 410}:
                last_error = f"{endpoint} returned {response.status_code}: {response.text}"
                break

            if response.status_code != 200:
                raise RuntimeError(f"Failed to retrieve Jira issues: {response.status_code} {response.text}")

            payload = response.json()
            issues = payload.get("issues", [])
            if not isinstance(issues, list):
                raise RuntimeError("Unexpected Jira response format for issues")

            collected.extend(issue for issue in issues if isinstance(issue, dict))
            print(f"Retrieved {len(collected)} issues so far...")

            total = int(payload.get("total", len(collected)))
            start_at += len(issues)
            if len(issues) == 0 or start_at >= total:
                return collected

    raise RuntimeError(f"Failed to query Jira issues. Last error: {last_error}")


def get_docs(application_doc: Any) -> list[dict[str, Any]] | dop.DopError:
    base_url = str(application_doc.settings.get("jira.url", "")).rstrip("/")
    token = str(application_doc.settings.get("jira.token", "")).strip()
    jql = str(
        application_doc.settings.get(
            "jira.jql",
            "assignee = currentUser() ORDER BY updated DESC",
        )
    ).strip()
    verify_ssl = _to_bool(application_doc.settings.get("jira.verify_ssl", True), default=True)

    if not base_url:
        return dop.error("Please specify Jira URL in Settings.")
    if not token:
        return dop.error("Please specify Jira token in Settings.")
    if not jql:
        return dop.error("Please specify Jira JQL in Settings.")

    try:
        headers, auth = _auth_config(application_doc)
        issues = _search_issues(base_url, jql, headers, auth, verify_ssl)
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    docs: list[dict[str, Any]] = []
    for issue in issues:
        fields = issue.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}

        key = str(issue.get("key", "")).strip()
        summary = str(fields.get("summary", "")).strip()

        status = fields.get("status", {})
        priority = fields.get("priority", {})
        issue_type = fields.get("issuetype", {})
        project = fields.get("project", {})
        assignee = fields.get("assignee", {})
        reporter = fields.get("reporter", {})

        status_name = status.get("name") if isinstance(status, dict) else None
        priority_name = priority.get("name") if isinstance(priority, dict) else None
        issue_type_name = issue_type.get("name") if isinstance(issue_type, dict) else None
        project_key = project.get("key") if isinstance(project, dict) else None
        project_name = project.get("name") if isinstance(project, dict) else None
        assignee_name = assignee.get("displayName") if isinstance(assignee, dict) else None
        reporter_name = reporter.get("displayName") if isinstance(reporter, dict) else None

        docs.append(
            {
                "key": key,
                "name": summary or key,
                "url": f"{base_url}/browse/{key}" if key else None,
                "summary": summary,
                "status": status_name,
                "priority": priority_name,
                "issue_type": issue_type_name,
                "project_key": project_key,
                "project_name": project_name,
                "assignee": assignee_name,
                "reporter": reporter_name,
                "created": fields.get("created"),
                "updated": fields.get("updated"),
                "description": fields.get("description"),
                "comment": fields.get("comment"),
                "worklog": fields.get("worklog"),
                "changelog": issue.get("changelog"),
            }
        )

    print(f"Found {len(docs)} issues.")
    return docs
