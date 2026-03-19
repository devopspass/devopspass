import asyncio
import importlib.util
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from agents import AgentRunner
from db import DopDoc
from jobs import RuntimeApplicationDoc
import dop


ENV_SUMMARY_PROMPT = """
You're DevOps investigator, your goal is to find out information about DevOps related data using locally stored info.
Someone already gathered some information about product, so called "DevOps Facts".
It text will be below.

Now your goal is to gather information about "Points of Configuration" (places where environment configuration happens) for a specific Product environment.
You have to gather all possible information about DevOps related stuff for environment, specifically, if possible:
* Where environment is deployed (AWS accounts, Kubernetes clusters, regions, etc.)
* Where "Points of Configuration" are placed
* Where placed and how looks deployment pipelines (pipelines, ArgoCD links, Flux, etc)
* What is the process of changes deployment to that specific envrionment
* Where possible add refs and links to "Points of Configuration"; for example: relative path to env config in repository with web-link to that file in GitLab

In current folder you can see files `doc.*.json` it short information about linked resources in JSON format (type, name, url, short description, etc.).
Each JSON file may contain field "local_path", it will be path to file or folder with "local" representation of that object (cloned repo, kube config, etc.)

In file `doc.*.md` will be "DevOps Facts" already gathered about that object (repo, aws account, k8s cluster, etc.).
You may use both `doc.*.md` and `doc.*.json` as a reference.

If possible check actual information in locally stored objects (git repos, configs, etc.).

- - - - - -
Overall product info:

{product_facts}
- - - - - -
Short env description:

{env_info}
- - - - - -
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


def _resolve_get_locally_source(dop_app: RuntimeApplicationDoc, doc_type: str) -> Path | None:
    doc_type_entry = _find_doc_type_entry(dop_app, doc_type)
    if doc_type_entry is None:
        return None
    source = doc_type_entry.get("get_locally")
    if not isinstance(source, str) or not source.strip():
        return None
    plugins_dir = _plugins_dir().resolve()
    source_path = (plugins_dir / source).resolve()
    if not source_path.exists():
        return None
    if not source_path.is_relative_to(plugins_dir):
        return None
    return source_path


def _load_module(source_path: Path) -> Any:
    module_name = f"dop_plugin_{source_path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {source_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _try_get_locally(
    resource_dop_app: RuntimeApplicationDoc,
    resource_doc: RuntimeApplicationDoc,
) -> str | None:
    """Try to get a local path for a resource. Returns path string or None."""
    source_path = _resolve_get_locally_source(resource_dop_app, resource_doc.doc_type)
    if source_path is None:
        return None

    try:
        module = _load_module(source_path)
        get_locally = getattr(module, "get_locally", None)
        if not callable(get_locally):
            return None

        workspace_folder = str(resource_dop_app.settings.get("user.workspace_folder", "")).strip()
        if not workspace_folder:
            return None

        result = get_locally(resource_doc, workspace_folder)

        if isinstance(result, dop.DopError):
            print(f"get_locally error for {resource_doc.doc_type}: {result}", flush=True)
            return None

        if isinstance(result, str):
            path = result.strip()
        elif isinstance(result, dict):
            path = str(result.get("path", "")).strip()
        else:
            return None

        if path and os.path.exists(path):
            return path
    except Exception as exc:  # noqa: BLE001
        print(f"get_locally failed for {resource_doc.doc_type}: {exc}", flush=True)

    return None


def _safe_filename(s: str) -> str:
    return s.replace("/", "_").replace(" ", "_").replace(":", "_")


def _run_agent_sync(
    runner: AgentRunner,
    ws_path: str,
    product_facts: str,
    env_info: str,
) -> dict[str, Any]:
    print(f"Starting agent in {ws_path}", flush=True)
    system_prompt = (
        ENV_SUMMARY_PROMPT
        .replace("{product_facts}", product_facts)
        .replace("{env_info}", env_info)
        + f"\n\nPut your result into {ws_path}/result.md. Do not return results in agent output, only write to the file."
    )
    return asyncio.run(
        runner.run_agent(
            session_id=str(uuid.uuid4()),
            system_prompt=system_prompt,
            user_message="",
            cwd=ws_path,
            mcp_servers=None,
        )
    )


def _update_doc_fact(doc: RuntimeApplicationDoc, text: str) -> None:
    dop.db.update_doc_fact(doc.app_id, doc.doc_type, doc.content, text)


def do_action(dop_app: RuntimeApplicationDoc, doc: RuntimeApplicationDoc, action_name: str) -> dict[str, Any]:
    if doc.doc_type != "dop_env":
        return dop.error(f"Unsupported doc_type: {doc.doc_type}")

    # Resolve parent product via product_id stored in env content.
    product_id = str(doc.content.get("product_id") or "").strip()
    if not product_id:
        # print(doc)
        product_id = str(doc.app_id or "").strip()
    if not product_id:
        return dop.error("Environment has no product_id — was it created by DevOps Summary on a product?")

    try:
        database = dop.db.get_database()
    except RuntimeError as exc:
        return dop.error(str(exc))

    # Load dop_product doc.
    product_docs = database.list_docs(doc_type="dop_product", app_id=product_id)
    if not product_docs:
        return dop.error(f"Parent product '{product_id}' not found in database")

    product_doc = product_docs[0]
    product_facts = product_doc.fact or ""

    resources = product_doc.content.get("resources", [])
    if not isinstance(resources, list) or not resources:
        return dop.error(
            "Parent product has no resources. Please attach resources and generate DevOps facts first."
        )

    # Build env description string.
    env_name = str(doc.content.get("name") or "").strip()
    env_type = str(doc.content.get("type") or "").strip()
    env_description = str(doc.content.get("description") or "").strip()
    env_info_parts = [f"Name: {env_name}"]
    if env_type:
        env_info_parts.append(f"Type: {env_type}")
    if env_description:
        env_info_parts.append(f"Description: {env_description}")
    env_info = "\n".join(env_info_parts)

    runner = AgentRunner()

    with tempfile.TemporaryDirectory(prefix="dop-env-agent-") as temp_dir:
        ws_path = temp_dir
        print(f"Workspace: {ws_path}", flush=True)

        # For each product resource, write doc.*.json and (if available) doc.*.md.
        # Also attempt get_locally() and embed local_path into the JSON.
        files_written = 0
        has_facts = False

        for resource in resources:
            if not isinstance(resource, dict):
                continue

            resource_app_id = resource.get("app_id")
            resource_doc_type = resource.get("doc_type")
            resource_name = str(resource.get("name", "")).strip()
            resource_url = str(resource.get("url") or "").strip()

            if not resource_doc_type or not resource_name:
                continue

            # Find the matching doc in the database.
            db_docs = database.list_docs(doc_type=resource_doc_type, app_id=resource_app_id)
            matched = next(
                (
                    d for d in db_docs
                    if d.app_id == resource_app_id
                    and d.doc_type == resource_doc_type
                    and str(d.content.get("name", "")).strip() == resource_name
                    and (
                        not resource_url
                        or str(d.content.get("url", "")).strip() == resource_url
                    )
                ),
                None,
            )

            if matched is None:
                print(f"Skipping {resource_doc_type}/{resource_name}: not found in database", flush=True)
                continue

            safe_prefix = f"{_safe_filename(str(resource_app_id or ''))}.{_safe_filename(resource_doc_type)}.{_safe_filename(resource_name)}"

            # Build JSON descriptor.
            json_data: dict[str, Any] = {
                "doc_type": resource_doc_type,
                "name": resource_name,
            }
            if resource_url:
                json_data["url"] = resource_url
            if matched.content.get("description"):
                json_data["description"] = matched.content["description"]

            # Attempt get_locally for this resource.
            # We need the application doc that owns this resource to get settings.
            resource_app_doc = None
            if resource_app_id:
                app_docs = database.list_docs(doc_type="dop_app", app_id=resource_app_id)
                if app_docs:
                    resource_app_doc = app_docs[0]

            if resource_app_doc is not None:
                resource_runtime_app = RuntimeApplicationDoc(
                    id=int(resource_app_doc.id or 0),
                    app_id=resource_app_doc.app_id,
                    doc_type=str(resource_app_doc.doc_type),
                    settings=dict(resource_app_doc.content.get("settings", {})),
                    content=dict(resource_app_doc.content),
                )
                resource_runtime_doc = RuntimeApplicationDoc(
                    id=int(matched.id or 0),
                    app_id=matched.app_id,
                    doc_type=str(matched.doc_type),
                    settings={},
                    content=dict(matched.content),
                )
                local_path = _try_get_locally(resource_runtime_app, resource_runtime_doc)
                if local_path:
                    json_data["local_path"] = local_path
                    print(f"Got local path for {resource_doc_type}/{resource_name}: {local_path}", flush=True)

            json_path = os.path.join(ws_path, f"doc.{safe_prefix}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)

            files_written += 1

            # Write facts markdown if available.
            if matched.fact:
                has_facts = True
                header_lines = [
                    "---",
                    f"doc_type: {resource_doc_type}",
                    f"name: {resource_name}",
                ]
                if resource_url:
                    header_lines.append(f"url: {resource_url}")
                header_lines.append("---\n")
                header = "\n".join(header_lines) + "\n"

                md_path = os.path.join(ws_path, f"doc.{safe_prefix}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(header + matched.fact)

                print(f"Written facts for {resource_doc_type}/{resource_name}", flush=True)

        if files_written == 0:
            return dop.error("No resource docs found — please attach resources to the parent product")

        if not has_facts:
            return dop.error(
                "No resource facts found — generate DevOps facts for the product's resources first"
            )

        try:
            result = _run_agent_sync(runner, ws_path, product_facts, env_info)
        except Exception as exc:  # noqa: BLE001
            return dop.error(f"Agent run failed: {exc}")

        output = result.get("output", "")
        print(f"Agent output length: {len(output)}", flush=True)

        result_path = os.path.join(ws_path, "result.md")
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                content = f.read()
                print(f"Read result.md length: {len(content)}", flush=True)
        except OSError as exc:
            return dop.error(f"Failed to read result.md: {exc}")

        try:
            _update_doc_fact(doc, content)
        except Exception as exc:  # noqa: BLE001
            return dop.error(f"Failed to update doc fact: {exc}")

    return {
        "status": "success",
        "message": "Environment DevOps summary generated",
        "content": content,
    }
