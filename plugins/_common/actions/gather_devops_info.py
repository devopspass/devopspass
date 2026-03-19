import asyncio
import importlib.util
import os
import uuid
from pathlib import Path
from typing import Any

from agents import AgentRunner
from jobs import RuntimeApplicationDoc
import dop


DEVOPS_PROMPT = """
You're DevOps assistant.
Read repo in current folder and get all information important from DevOps perspective.
Do not provide example, just plain facts about repo.
Focus on CI/CD, infrastructure as code, monitoring, security, secrets management and other DevOps related topics.

- If repo is application source, check which configs it has, in dependencies check which integrations it may use (DB, queues, external APIs, key/value, etc).
- If you see you see configs for different components/envs, make a summary about components/envs list
- If possible build "matrix of components", which env which component is deployed, which versions. Usually amount of envs are less then components, so make envs columns, components rows.
- If you can put link to descripbed resource, like repo, cluster.

Results write to CWD/result.md
"""


def _plugins_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _find_doc_type_entry(dop_app: RuntimeApplicationDoc, doc_type: str) -> dict[str, Any] | None:
    raw_doc_types = dop_app.content.get("doc_types", [])
    if not isinstance(raw_doc_types, list):
        return None

    for item in raw_doc_types:
        if not isinstance(item, dict):
            continue
        if str(item.get("key", "")).strip() == doc_type:
            return item
    return None


def _resolve_get_locally_source(dop_app: RuntimeApplicationDoc, doc_type: str) -> Path:
    doc_type_entry = _find_doc_type_entry(dop_app, doc_type)
    if doc_type_entry is None:
        raise ValueError(f"Doc type {doc_type} is not configured in application")

    source = doc_type_entry.get("get_locally")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"Doc type {doc_type} does not define get_locally")

    plugins_dir = _plugins_dir().resolve()
    source_path = (plugins_dir / source).resolve()
    if not source_path.exists():
        raise ValueError(f"Plugin source not found: {source}")

    if not source_path.is_relative_to(plugins_dir):
        raise ValueError("get_locally source is outside plugins directory")

    return source_path


def _load_get_locally_module(dop_app: RuntimeApplicationDoc, doc_type: str) -> Any:
    source_path = _resolve_get_locally_source(dop_app, doc_type)
    module_name = f"dop_plugin_get_locally_{source_path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load get_locally module from {source_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_locally(dop_app: RuntimeApplicationDoc, doc: RuntimeApplicationDoc) -> str:
    workspace_folder = str(dop_app.settings.get("user.workspace_folder", "/workspace/git/")).strip()
    if not workspace_folder:
        raise ValueError("Workspace folder is not configured")

    module = _load_get_locally_module(dop_app, doc.doc_type)
    get_locally = getattr(module, "get_locally", None)
    if not callable(get_locally):
        raise ValueError("Plugin does not expose get_locally(application_doc, workspace_folder)")

    application_doc = RuntimeApplicationDoc(
        id=doc.id,
        app_id=doc.app_id,
        doc_type=doc.doc_type,
        settings=dict(dop_app.settings),
        content=dict(doc.content),
    )
    result = get_locally(application_doc, workspace_folder)

    if isinstance(result, dop.DopError):
        raise ValueError(str(result))

    if isinstance(result, str):
        repo_path = result.strip()
    elif isinstance(result, dict):
        repo_path = str(result.get("path", "")).strip()
    else:
        raise ValueError("get_locally() returned unsupported result type")

    if not repo_path:
        raise ValueError("get_locally() did not return repository path")

    if not os.path.isdir(repo_path):
        raise ValueError(f"Local repository path does not exist: {repo_path}")

    return repo_path


def _run_agent_sync(runner: AgentRunner, repo_path: str) -> dict[str, Any]:
    print(f"Starting ACP agent in {repo_path}", flush=True)

    def _on_agent_event(text: str) -> None:
        # Forward formatted activity lines to job logs in real time.
        print(text, flush=True)

    return asyncio.run(
        runner.run_agent(
            session_id=str(uuid.uuid4()),
            system_prompt=DEVOPS_PROMPT,
            user_message="",
            cwd=repo_path,
            mcp_servers=None,
            event_callback=_on_agent_event,
        )
    )


def _update_doc_fact(doc: RuntimeApplicationDoc, text: str) -> None:
    dop.db.update_doc_fact(doc.app_id, doc.doc_type, doc.content, text)


def do_action(dop_app: RuntimeApplicationDoc, doc: RuntimeApplicationDoc, action_name: str) -> dict[str, Any]:
    if dop_app is None:
        return dop.error("Application context is required for get_locally")

    try:
        repo_path = _get_locally(dop_app, doc)
    except Exception as exc:  # noqa: BLE001
        return dop.error(str(exc))

    url = doc.content.get("url")
    print(f"Preparing DevOps summary for {url or doc.doc_type}", flush=True)

    runner = AgentRunner()

    try:
        result = _run_agent_sync(runner, repo_path)
    except Exception as exc:  # noqa: BLE001
        return dop.error(f"Agent run failed: {exc}")

    output = result.get("output", "")
    print(f"Agent output length: {len(output)}", flush=True)
    result_path = os.path.join(repo_path, "result.md")
    # Read result.md generated by agent and return content as result.
    try:
        with open(result_path, "r", encoding="utf-8") as file:
            content = file.read()
            print(f"Read result.md content length: {len(content)}", flush=True)
    except OSError as exc:
        return dop.error(f"Failed to read result.md: {exc}")

    try:
        _update_doc_fact(doc, content)
    except Exception as exc:  # noqa: BLE001
        return dop.error(f"Failed to update doc fact: {exc}")

    return {
        "status": "success",
        "message": "DevOps summary generated",
        "path": result_path,
        "content": content,
    }
