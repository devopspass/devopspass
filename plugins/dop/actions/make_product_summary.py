import asyncio
import json
import os
import tempfile
import uuid
from typing import Any

from agents import AgentRunner
from db import DopDoc
from jobs import RuntimeApplicationDoc
import dop


DEVOPS_PROMPT = """
You're DevOps assistant.
Right now, you're gathering information about Product and it's enviornments.
Product it's one or more applications, that are developed and maintained as one logical business component.
Like product CRM may contain multiple applications: frontend, backend, mobile app, infrastructure, etc.
But all of them are part of one product because they serve one business goal and are developed together.
Your goal is to gather as much information as possible about Product and it's environments, that may be useful for DevOps tasks.

# Input

You're in folder with .md files, each one is generated facts about some aspect of Product or its environments.
Like facts for terraform code repo, facts about kubernetes cluster, etc.
Your goal - read all of them one by one and pick necessary information to generate summary about Product and its envs.
Each file starting from header with info about doc it was generated for.

# Output

## file `summary.md`

summary about product,should include DevOps information with refs to make "points of configuration"
Where configs, where deployed, etc.
Do not put too much details about environments, detailed will be gathered in separate tasks.
If possible build "matrix of components", which env which conmonent is deployed, which versions. Usually amount of envs are less then components, so make envs columns, components rows.
If you can put link to descripbed resource, like repo, cluster, etc - do it.


## file `envs.json`

json with list of envs, details about each env will be gathered in separate jobs.
   `envs.json` structure:
    [
        {
            "name": "env name",
            "type": "env type, e.g. production, staging, development, etc",
            "details": "short summary, 1-2 sentences."
        }
    ]
"""

def _run_agent_sync(runner: AgentRunner, repo_path: str, doc: RuntimeApplicationDoc) -> dict[str, Any]:
    print(f"Starting ACP agent in {repo_path}", flush=True)
    system_prompt = (
        DEVOPS_PROMPT
        + f"\n\nImportant: put results into {repo_path}/summary.md and {repo_path}/envs.json files respectively. Do not return results in agent output, only write to files."
    )
    # if doc.content.prompt is not empty, use it as addition to system prompt
    # to give more specific instructions for summary generation.
    if doc.content.get("prompt"):
        system_prompt += "\n\nAdditional instructions from user:\n" + doc.content["prompt"]
    return asyncio.run(
        runner.run_agent(
            session_id=str(uuid.uuid4()),
            system_prompt=system_prompt,
            user_message="",
            cwd=repo_path,
            mcp_servers=None,
        )
    )


def _update_doc_fact(doc: RuntimeApplicationDoc, text: str) -> None:
    dop.db.update_doc_fact(doc.app_id, doc.doc_type, doc.content, text)


def do_action(dop_app: RuntimeApplicationDoc, doc: RuntimeApplicationDoc, action_name: str) -> dict[str, Any]:
    if doc.doc_type != "dop_product":
        return dop.error(f"Unsupported doc_type: {doc.doc_type}")

    resources = doc.content.get("resources", [])
    if not isinstance(resources, list) or not resources:
        return dop.error("Product has no resources to generate summary from. Please attach some")

    runner = AgentRunner()

    with tempfile.TemporaryDirectory(prefix="dop-agent-") as temp_dir:
        ws_path = temp_dir
        print(f"Workspace {ws_path}", flush=True)

        # Get all related resources facts.
        # If a resource has facts, write them into the workspace as separate files:
        # <app_id>.<doc_type>.<name>.md
        # Each file starts with a YAML front-matter header:
        #   ---
        #   doc_type: <doc_type>
        #   name: <name>
        #   url: <url>        # (if available)
        #   ---
        try:
            database = dop.db.get_database()
        except RuntimeError as exc:
            return dop.error(str(exc))

        files_written = 0
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

            if matched is None or not matched.fact:
                print(f"Skipping {resource_doc_type}/{resource_name}: no facts available", flush=True)
                continue

            # Build a safe filename.
            def _safe(s: str) -> str:
                return s.replace("/", "_").replace(" ", "_").replace(":", "_")

            filename = f"{_safe(str(resource_app_id or ''))}.{_safe(resource_doc_type)}.{_safe(resource_name)}.md"
            file_path = os.path.join(ws_path, filename)

            header_lines = ["---", f"doc_type: {resource_doc_type}", f"name: {resource_name}"]
            if resource_url:
                header_lines.append(f"url: {resource_url}")
            header_lines.append("---\n")
            header = "\n".join(header_lines) + "\n"

            with open(file_path, "w") as f:
                f.write(header + matched.fact)

            print(f"Written facts for {resource_doc_type}/{resource_name} → {filename}", flush=True)
            files_written += 1

        if files_written == 0:
            return dop.error("No resource facts found — generate facts for resources first")

        try:
            result = _run_agent_sync(runner, ws_path, doc)
        except Exception as exc:  # noqa: BLE001
            return dop.error(f"Agent run failed: {exc}")

        # Agent have to create two files:
        #  - summary.md with product summary and
        #  - envs.json with list of envs.
        output = result.get("output", "")
        print(f"Agent output length: {len(output)}", flush=True)

        summary_path = os.path.join(ws_path, "summary.md")
        try:
            with open(summary_path, "r") as f:
                content = f.read()
                print(f"Read summary.md content length: {len(content)}", flush=True)
        except OSError as exc:
            return dop.error(f"Failed to read summary.md: {exc}")

        try:
            _update_doc_fact(doc, content)
        except Exception as exc:  # noqa: BLE001
            return dop.error(f"Failed to update doc fact: {exc}")

        # Now, check if envs.json file exists, read it.
        # Check if related environments docs exist in the database, if yes - do nothing.
        # If not - create a new dop_env doc for each environment.
        envs_path = os.path.join(ws_path, "envs.json")
        envs_created: list[str] = []
        if os.path.exists(envs_path):
            try:
                with open(envs_path, "r") as f:
                    envs_data = json.load(f)

                if isinstance(envs_data, list):
                    product_app_id = doc.app_id
                    existing_envs = database.list_docs(doc_type="dop_env", app_id=product_app_id)
                    existing_names = {
                        str(d.content.get("name", "")).strip()
                        for d in existing_envs
                        if str(d.content.get("name", "")).strip()
                    }

                    for env in envs_data:
                        if not isinstance(env, dict):
                            continue
                        env_name = str(env.get("name") or "").strip()
                        if not env_name:
                            continue

                        # Check if a dop_env doc already exists for this product + env name.
                        if env_name in existing_names:
                            print(f"Environment '{env_name}' already exists, skipping", flush=True)
                            continue

                        env_content = {
                            "name": env_name,
                            "type": str(env.get("type") or "").strip(),
                            "description": str(env.get("details") or "").strip(),
                            "product_id": product_app_id,
                        }
                        database.add_doc(DopDoc(
                            app_id=product_app_id,
                            doc_type="dop_env",
                            content=env_content,
                        ))
                        existing_names.add(env_name)
                        envs_created.append(env_name)
                        print(f"Created dop_env doc for environment '{env_name}'", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: failed to process envs.json: {exc}", flush=True)

        return {
            "status": "success",
            "message": "Product summary generated",
            "path": summary_path,
            "content": content,
            "envs_created": envs_created,
        }
