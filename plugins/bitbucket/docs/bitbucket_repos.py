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


def _build_auth(application_doc: Any, variant: str) -> tuple[dict[str, str], HTTPBasicAuth | None]:
    username = str(application_doc.settings.get("bitbucket.username", "")).strip()
    app_password = str(application_doc.settings.get("bitbucket.app_password", "")).strip()
    token = str(application_doc.settings.get("bitbucket.token", "")).strip()

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


def _request_json(
    url: str,
    headers: dict[str, str],
    auth: HTTPBasicAuth | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.get(url, headers=headers, auth=auth, params=params, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Bitbucket API request failed ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Bitbucket API response format")
    return payload


def _cloud_clone_url(repo: dict[str, Any], clone_name: str) -> str:
    links = repo.get("links")
    if not isinstance(links, dict):
        return ""
    clone_links = links.get("clone")
    if not isinstance(clone_links, list):
        return ""

    for entry in clone_links:
        if isinstance(entry, dict) and entry.get("name") == clone_name:
            href = entry.get("href")
            if isinstance(href, str):
                return href.strip()
    return ""


def _server_clone_url(repo: dict[str, Any], clone_name: str) -> str:
    links = repo.get("links")
    if not isinstance(links, dict):
        return ""
    clone_links = links.get("clone")
    if not isinstance(clone_links, list):
        return ""

    accepted_names = {clone_name}
    if clone_name == "https":
        accepted_names.add("http")

    for entry in clone_links:
        if isinstance(entry, dict) and entry.get("name") in accepted_names:
            href = entry.get("href")
            if isinstance(href, str):
                return href.strip()
    return ""


def _links_markdown(url: str, variant: str) -> str:
    if not url:
        return ""
    if variant == "cloud":
        return (
            f"\n- [Pull requests]({url}/pull-requests)"
            f"\n- [Branches]({url}/branches)"
            f"\n- [Commits]({url}/commits)"
            f"\n- [Source]({url}/src)"
        )

    return (
        f"\n- [Pull requests]({url}/pull-requests)"
        f"\n- [Branches]({url}/branches)"
        f"\n- [Commits]({url}/commits)"
        f"\n- [Browse]({url}/browse)"
    )


def _cloud_repo_to_doc(repo: dict[str, Any], server: str) -> dict[str, Any]:
    full_name = str(repo.get("full_name") or "").strip()
    workspace_slug, _, slug = full_name.partition("/")
    links = repo.get("links") if isinstance(repo.get("links"), dict) else {}
    html_link = links.get("html") if isinstance(links, dict) else {}
    html_url = str(html_link.get("href") or "").strip() if isinstance(html_link, dict) else ""
    project = repo.get("project") if isinstance(repo.get("project"), dict) else {}
    project_name = str(project.get("name") or workspace_slug).strip() or workspace_slug

    return {
        "name": repo.get("name"),
        "slug": slug,
        "workspace": workspace_slug,
        "workspace_slug": workspace_slug,
        "project": project_name,
        "project_key": workspace_slug,
        "full_name": full_name,
        "url": html_url or f"{server}/{full_name}",
        "description": repo.get("description") or "",
        "ssh_url_to_repo": _cloud_clone_url(repo, "ssh"),
        "clone_url": _cloud_clone_url(repo, "https"),
        "variant": "cloud",
        "links": _links_markdown(html_url or f"{server}/{full_name}", "cloud"),
    }


def _server_repo_to_doc(repo: dict[str, Any], server: str) -> dict[str, Any]:
    project = repo.get("project") if isinstance(repo.get("project"), dict) else {}
    project_key = str(project.get("key") or "").strip()
    project_name = str(project.get("name") or project_key).strip() or project_key
    slug = str(repo.get("slug") or "").strip()
    url = f"{server}/projects/{project_key}/repos/{slug}" if project_key and slug else ""
    full_name = f"{project_key}/{slug}" if project_key and slug else slug

    return {
        "name": repo.get("name"),
        "slug": slug,
        "workspace": project_key,
        "workspace_slug": project_key,
        "project": project_name,
        "project_key": project_key,
        "full_name": full_name,
        "url": url,
        "description": repo.get("description") or "",
        "ssh_url_to_repo": _server_clone_url(repo, "ssh"),
        "clone_url": _server_clone_url(repo, "https"),
        "variant": "server",
        "links": _links_markdown(url, "server"),
    }


def _fetch_cloud_repositories(
    api_url: str,
    headers: dict[str, str],
    auth: HTTPBasicAuth | None,
    server: str,
    workspace: str,
) -> list[dict[str, Any]]:
    # GET /repositories/{workspace} works with both app passwords and workspace/repo access tokens
    next_url: str | None = f"{api_url}/repositories/{workspace}"
    repos: list[dict[str, Any]] = []

    while next_url:
        payload = _request_json(next_url, headers, auth, params={"pagelen": 100, "sort": "name"} if "?" not in next_url else None)
        next_url = payload.get("next") if isinstance(payload.get("next"), str) else None
        values = payload.get("values")
        if not isinstance(values, list):
            break

        for repo in values:
            if isinstance(repo, dict):
                repos.append(_cloud_repo_to_doc(repo, server))
        print(f"Retrieved {len(repos)} repositories so far...")

    return repos


def _fetch_server_repositories(
    api_url: str,
    headers: dict[str, str],
    auth: HTTPBasicAuth | None,
    server: str,
) -> list[dict[str, Any]]:
    start = 0
    repos: list[dict[str, Any]] = []

    while True:
        payload = _request_json(f"{api_url}/repos", headers, auth, params={"limit": 100, "start": start})
        values = payload.get("values")
        if isinstance(values, list):
            for repo in values:
                if isinstance(repo, dict):
                    repos.append(_server_repo_to_doc(repo, server))
            print(f"Retrieved {len(repos)} repositories so far...")

        if payload.get("isLastPage", True):
            break

        next_start = payload.get("nextPageStart")
        if not isinstance(next_start, int):
            break
        start = next_start

    return repos


def get_docs(application_doc: Any) -> list[dict[str, Any]] | dop.DopError:
    variant = _normalize_variant(str(application_doc.settings.get("bitbucket.variant", "cloud")))
    default_server = "https://bitbucket.org" if variant == "cloud" else ""
    server = _normalize_server_url(str(application_doc.settings.get("bitbucket.server", default_server)), default_server)
    api_url = _normalize_api_url(server, str(application_doc.settings.get("bitbucket.api_url", "")), variant)

    workspace = str(application_doc.settings.get("bitbucket.workspace", "")).strip()

    try:
        headers, auth = _build_auth(application_doc, variant)
        if variant == "cloud":
            if not workspace:
                return dop.error("Please set bitbucket.workspace to your Bitbucket Cloud workspace slug.")
            docs = _fetch_cloud_repositories(api_url, headers, auth, server, workspace)
        else:
            if not server:
                return dop.error("Please specify Bitbucket server URL in Settings.")
            docs = _fetch_server_repositories(api_url, headers, auth, server)
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    print(f"Found {len(docs)} repositories.")
    return docs
