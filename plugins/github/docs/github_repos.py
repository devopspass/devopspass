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


def _parse_orgs(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    orgs = [part.strip() for part in value.split(",")]
    return [org for org in orgs if org]


def _request_json(url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str | None]:
    response = requests.get(url, headers=headers, params=params, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"GitHub API request failed ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected GitHub API response format")

    items = [item for item in payload if isinstance(item, dict)]
    next_url = response.links.get("next", {}).get("url")
    return items, next_url


def _fetch_all(url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    request_params = params

    while next_url:
        page_items, next_url = _request_json(next_url, headers, request_params)
        request_params = None
        items.extend(page_items)
        print(f"Retrieved {len(items)} repositories so far...")

    return items


def _repo_to_doc(repo: dict[str, Any], server: str) -> dict[str, Any]:
    html_url = str(repo.get("html_url") or "").strip()
    full_name = str(repo.get("full_name") or "").strip()

    links = ""
    if html_url:
        links = (
            f"\n- [Pull requests]({html_url}/pulls)"
            f"\n- [Issues]({html_url}/issues)"
            f"\n- [Actions]({html_url}/actions)"
            f"\n- [Branches]({html_url}/branches)"
            f"\n- [Tags]({html_url}/tags)"
            f"\n- [Releases]({html_url}/releases)"
        )

    owner = repo.get("owner")
    owner_login = owner.get("login") if isinstance(owner, dict) else None

    return {
        "id": repo.get("id"),
        "name": repo.get("name"),
        "full_name": full_name,
        "url": html_url,
        "description": repo.get("description") or "",
        "default_branch": repo.get("default_branch"),
        "visibility": repo.get("visibility") or ("private" if repo.get("private") else "public"),
        "is_private": repo.get("private", False),
        "is_fork": repo.get("fork", False),
        "language": repo.get("language"),
        "owner": owner_login,
        "ssh_url_to_repo": repo.get("ssh_url"),
        "clone_url": repo.get("clone_url"),
        "links": links,
        "server": server,
    }


def get_docs(application_doc: Any) -> list[dict[str, Any]] | dop.DopError:
    server = _normalize_server_url(str(application_doc.settings.get("github.server", "https://github.com")))
    api_url = _normalize_api_url(server, str(application_doc.settings.get("github.api_url", "https://api.github.com")))
    token = str(application_doc.settings.get("github.token", "")).strip()
    orgs = _parse_orgs(application_doc.settings.get("github.orgs", ""))

    if not token:
        return dop.error("Please specify GitHub token in Settings.")
    if not api_url:
        return dop.error("Please specify GitHub API URL in Settings.")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        repos: list[dict[str, Any]] = []
        if orgs:
            for org in orgs:
                org_repos = _fetch_all(
                    f"{api_url}/orgs/{org}/repos",
                    headers,
                    params={"type": "all", "per_page": 100, "sort": "updated"},
                )
                repos.extend(org_repos)
                print(f"Retrieved {len(org_repos)} repositories for org {org}.")
        else:
            repos = _fetch_all(
                f"{api_url}/user/repos",
                headers,
                params={"visibility": "all", "affiliation": "owner", "per_page": 100, "sort": "updated"},
            )
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    docs = [_repo_to_doc(repo, server) for repo in repos]
    print(f"Found {len(docs)} repositories.")
    return docs
