import os
import subprocess
from typing import Any
from urllib.parse import urlparse

import dop
from jobs import RuntimeApplicationDoc, run_command


def _clone_source_key(doc_type: str | None) -> str | None:
    if doc_type == 'gitlab_repos':
        return 'gitlab.clone_source'
    if doc_type == 'github_repos':
        return 'github.clone_source'
    if doc_type == 'bitbucket_repos':
        return 'bitbucket.clone_source'
    return None


def _extract_domain_and_path(source: str) -> tuple[str, str]:
    trimmed = source.strip()
    if not trimmed:
        raise ValueError('Missing repository URL')

    if trimmed.startswith('git@'):
        host_and_path = trimmed[4:]
        if ':' not in host_and_path:
            raise ValueError(f'Invalid SSH clone URL: {trimmed}')
        domain, raw_path = host_and_path.split(':', 1)
        path = raw_path.rstrip('/')
        if path.endswith('.git'):
            path = path[:-4]
        return domain, path

    parsed_url = urlparse(trimmed)
    if parsed_url.scheme not in ('http', 'https'):
        raise ValueError(f'Invalid repository URL: {trimmed}')

    path = parsed_url.path.strip('/')
    if path.endswith('.git'):
        path = path[:-4]

    return parsed_url.netloc, path

def get_clone_url(
    url: str,
    doc_type: str | None,
    dop_app: RuntimeApplicationDoc,
    doc: RuntimeApplicationDoc | None = None,
) -> tuple[str, str, str]:
    """
    Determine the clone URL based on repository type and settings.

    Args:
        url: HTTPS URL of the repository
        doc_type: Type of document (e.g., 'gitlab_repos')
        dop_app: DOP application instance with settings

    Returns:
        Tuple of (clone_url, domain, path)
    """
    clone_source = 'https'
    clone_source_key = _clone_source_key(doc_type)
    if clone_source_key is not None:
        clone_source = str(dop_app.settings.get(clone_source_key, 'https')).strip().lower() or 'https'

    clone_url = ''
    doc_content = doc.content if doc is not None and hasattr(doc, 'content') else {}
    ssh_clone_url = doc_content.get('ssh_url_to_repo')
    https_clone_url = doc_content.get('clone_url')

    if clone_source == 'ssh':
        if isinstance(ssh_clone_url, str) and ssh_clone_url.strip():
            clone_url = ssh_clone_url.strip()
        else:
            domain, path = _extract_domain_and_path(url)
            clone_url = f"git@{domain}:{path}.git"
    elif clone_source == 'https':
        if isinstance(https_clone_url, str) and https_clone_url.strip():
            clone_url = https_clone_url.strip()
        else:
            clone_url = url
    else:
        raise ValueError(f'Invalid clone source setting, must be "ssh" or "https", got: {clone_source}')

    domain, path = _extract_domain_and_path(url)

    return clone_url, domain, path


def clone_repository(clone_url: str, repo_path: str, url: str) -> bool:
    """
    Clone a git repository if it doesn't already exist.

    Args:
        clone_url: URL to clone from (HTTPS or SSH)
        repo_path: Local path where repository should be cloned
        url: Original HTTPS URL for display purposes

    Returns:
        True if cloned successfully or already exists, False on error
    """
    if not os.path.exists(os.path.join(repo_path, '.git')):
        # Directory does not exist, perform git clone
        try:
            os.makedirs(repo_path, exist_ok=True)
            run_command(['git', 'clone', clone_url, repo_path], check=True)
            print(f"Cloned repository '{url}' ({clone_url}) into '{repo_path}'")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Git clone failed with exit code {e.returncode}")
            return False
    else:
        print(f"Repository already exists at '{repo_path}'")
        return True


def do_action(dop_app: RuntimeApplicationDoc, doc: RuntimeApplicationDoc, action_name: str) -> dict[str, Any]:
    """
    Main entry point for git clone action.

    Args:
        dop_app: DOP application instance with settings
        doc: Document containing metadata with repository URL
        action_name: Name of the action to perform

    Returns:
        Dictionary with status and repository path, or exits on error
    """

    # Extract URL from document metadata
    url = doc.content.get('url')
    if not url:
        raise ValueError('No URL found in document')

    # Get workspace folder from settings
    settings = dop_app.settings
    if settings is None:
        raise ValueError('Settings not available on dop_app')

    workspace_folder = dop_app.settings.get('user.workspace_folder', '/workspace/git/')
    if not workspace_folder:
        raise ValueError('Workspace folder not configured in settings')

    # Get document type for determining clone source
    doc_type = doc.doc_type

    # Determine clone URL and repository path
    clone_url, domain, path = get_clone_url(url, doc_type, dop_app, doc)
    repo_path = f"{workspace_folder}/{domain}/{path}"

    # Perform the clone operation
    success = clone_repository(clone_url, repo_path, url)

    if not success:
        return dop.error(f"Failed to clone repository from {clone_url}")

    print(f"Successfully cloned repository from {clone_url} to {repo_path}")

    return {
        "status": "success",
        "message": f"Repository available at {repo_path}",
        "path": repo_path,
        "clone_url": clone_url,
    }
