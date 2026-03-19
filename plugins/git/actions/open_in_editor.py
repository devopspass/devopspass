import importlib.util
import os
import uuid
from urllib.parse import urlparse, quote
from typing import Any

import yaml

from jobs import RuntimeApplicationDoc
import dop


def get_host_workspace_path_from_docker_compose() -> str:
    """
    Read docker-compose.yml from the container and extract the host path for /workspace/git/.

    Returns:
        Host path that is mounted to /workspace/git in the container

    Raises:
        ValueError: If docker-compose.yml is not readable or /workspace/git mapping not found
    """
    compose_file = '/workspace/docker-compose.yml'

    if not os.path.exists(compose_file):
        raise ValueError(
            f'docker-compose.yml not mounted at {compose_file}. '
            'Please add to your docker-compose.yml under api.volumes:\n'
            '  - ./docker-compose.yml:/workspace/docker-compose.yml:ro\n'
        )

    try:
        with open(compose_file, 'r') as f:
            compose_config = yaml.safe_load(f)
    except Exception as e:
        raise ValueError(f'Failed to parse docker-compose.yml: {str(e)}')

    if not compose_config or 'services' not in compose_config:
        raise ValueError('Invalid docker-compose.yml format')

    # Find the api service
    api_service = compose_config.get('services', {}).get('api', {})
    if not api_service:
        raise ValueError('No "api" service found in docker-compose.yml')

    # Find volumes that map to /workspace/git/
    volumes = api_service.get('volumes', [])
    if not isinstance(volumes, list):
        raise ValueError('Invalid volumes format in docker-compose.yml')

    for volume in volumes:
        if isinstance(volume, str):
            parts = volume.split(':')
            if len(parts) >= 2 and parts[1].startswith('/workspace/git'):
                host_path = parts[0]
                # Resolve relative paths (like ~/some/path or ./relative/path)
                if host_path.startswith('~'):
                    host_path = os.path.expanduser(host_path)
                elif host_path.startswith('.'):
                    # Can't resolve relative paths from inside container, suggest absolute path
                    raise ValueError(
                        f'Found relative path "{host_path}" for /workspace/git mount in docker-compose.yml. '
                        'Please change it to an absolute path, e.g.:\n'
                        f'  - /Users/yourname/path/to/workspace:/workspace/git\n'
                    )
                return host_path

    raise ValueError(
        'No volume mount found for /workspace/git/ in docker-compose.yml. '
        'Please add to your docker-compose.yml under api.volumes:\n'
        '  - /absolute/path/to/your/workspace:/workspace/git\n'
        'Example:\n'
        '  - /Users/aliakseikharyton/DEVEL/LUM/:/workspace/git\n'
    )


def generate_editor_url(host_repo_path: str, editor: str) -> str:
    """
    Generate an Editor URI to open a folder on the host machine.

    Args:
        host_repo_path: Full path to the repository on the host machine

    Returns:
        Editor URI for the selected editor

    Note:
        VSCode will typically reuse the last active window. There's no standard
        URI parameter to force a new window - that requires CLI flags like `code -n`.
    """
    normalized_editor = editor.strip().lower()

    # VSCode URI scheme: vscode://file/{path}
    # Path should be URL-encoded
    encoded_path = quote(host_repo_path, safe='/')

    if normalized_editor in ('vscode', 'vs code'):
        return f"vscode://file{encoded_path}"

    raise ValueError(f'Unsupported editor setting user.editor={editor!r}. Supported values: VSCode')


def map_container_path_to_host(container_path: str, host_workspace_path: str) -> str:
    """
    Map a container path to the corresponding host path.

    Args:
        container_path: Path inside the container (e.g., /workspace/git/domain/path)
        host_workspace_path: Host path that /workspace/git maps to

    Returns:
        Full path on the host machine

    Raises:
        ValueError: If container_path doesn't start with /workspace/git
    """
    container_prefix = '/workspace/git'
    if not container_path.startswith(container_prefix):
        raise ValueError(f'Container path must start with {container_prefix}')

    # Remove the container prefix and append to host path
    relative_path = container_path[len(container_prefix):].lstrip('/')
    host_path = os.path.join(host_workspace_path, relative_path) if relative_path else host_workspace_path

    return host_path


def load_clone_action() -> Any:
    """
    Load the clone_git action module from the same directory.

    Returns:
        The loaded module with do_action available.
    """
    source_path = os.path.join(os.path.dirname(__file__), 'clone_git.py')
    module_name = f"dop_plugin_clone_git_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load clone_git from {source_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def do_action(dop_app: RuntimeApplicationDoc, doc: RuntimeApplicationDoc, action_name: str) -> dict[str, Any]:
    """
    Main entry point for open-in-editor action.

    Returns a VSCode URI that can be used to open the repository on the host machine.

    Args:
        dop_app: DOP application instance with settings
        doc: Document containing metadata with repository URL
        action_name: Name of the action to perform

    Returns:
        Dictionary with status and VSCode URI to open the repository
    """
    # Extract URL from document metadata
    url = doc.content.get('url')
    if not url:
        raise ValueError('No URL found in document')

    # Parse and validate the URL
    parsed_url = urlparse(url)
    if parsed_url.scheme != 'https':
        raise ValueError('Invalid HTTPS URL')

    # Extract the domain and path
    domain = parsed_url.netloc
    path = parsed_url.path.strip('/')

    # Get workspace folder from settings
    settings = dop_app.settings
    if settings is None:
        raise ValueError('Settings not available on dop_app')

    workspace_folder = '/workspace/git/'

    # Construct repository path (container path)
    container_repo_path = f"{workspace_folder}/{domain}/{path}"

    # Ensure repository is cloned before opening
    clone_module = load_clone_action()
    clone_result = clone_module.do_action(dop_app, doc, "clone_git")
    if isinstance(clone_result, dop.DopError):
        return clone_result
    if isinstance(clone_result, str):
        return dop.error(clone_result)

    # Verify repository exists before returning the URL
    if not os.path.exists(os.path.join(container_repo_path, '.git')):
        return dop.error(f"Repository not found at '{container_repo_path}'. Clone it first.")

    # Get the host workspace path from docker-compose.yml
    try:
        host_workspace_path = get_host_workspace_path_from_docker_compose()
    except ValueError as e:
        return dop.error(str(e))

    # Map container path to host path
    try:
        host_repo_path = map_container_path_to_host(container_repo_path, host_workspace_path)
    except ValueError as e:
        return dop.error(f"Path mapping error: {str(e)}")

    # Resolve editor preference from DevOps Pass AI dop_app settings
    try:
        dop_settings = dop.settings.get_dop_app_settings('devops-pass-ai')
    except Exception:
        dop_settings = {}
    selected_editor = str(dop_settings.get('user.editor', 'VSCode'))

    # Generate editor URI
    try:
        editor_uri = generate_editor_url(host_repo_path, selected_editor)
    except ValueError as e:
        return dop.error(str(e))

    return {
        'status': 'success',
        'message': f"Repository available at {host_repo_path}",
        'path': host_repo_path,
        'uri': editor_uri
    }
