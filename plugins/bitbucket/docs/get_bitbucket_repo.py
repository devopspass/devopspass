import importlib.util
import os
import uuid
from pathlib import Path
from typing import Any

import dop
from jobs import RuntimeApplicationDoc


def _load_clone_module() -> Any:
    source_path = Path(__file__).resolve().parents[2] / "git" / "actions" / "clone_git.py"
    module_name = f"dop_plugin_clone_git_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load clone_git from {source_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_locally(application_doc: RuntimeApplicationDoc, workspace_folder: str) -> dict[str, Any]:
    url = application_doc.content.get("url")
    if not isinstance(url, str) or not url.strip():
        return dop.error("No URL found in document")

    workspace_root = workspace_folder.strip()
    if not workspace_root:
        return dop.error("Workspace folder is not configured")

    try:
        clone_module = _load_clone_module()
        clone_url, domain, path = clone_module.get_clone_url(
            url,
            application_doc.doc_type,
            application_doc,
            application_doc,
        )
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    repo_path = os.path.join(workspace_root, domain, path)
    success = clone_module.clone_repository(clone_url, repo_path, url)
    if not success:
        return dop.error(f"Failed to clone repository from {clone_url}")

    return {
        "status": "success",
        "message": f"Repository available at {repo_path}",
        "path": repo_path,
        "clone_url": clone_url,
    }
