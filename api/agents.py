import json
import os
import re
import subprocess
import selectors
from pathlib import Path
from typing import Any, Callable

import yaml

from agent_events import extract_final_message, format_event_for_display, parse_copilot_event_line
from db import Database, DopDoc
from dop.db import get_db_path
from dop.settings import get_dop_app_settings
from plugins import PluginRegistry


class AgentRunner:
    _agent_placeholder_pattern = re.compile(r"\{([^{}]+)\}")

    def __init__(self, data_dir: Path | None = None, plugins_dir: Path | None = None) -> None:
        self.plugins_dir = plugins_dir or Path(os.environ.get("DOP_PLUGINS_DIR", "/workspace/plugins"))
        self.db_path = get_db_path(data_dir)

    async def run_agent(
        self,
        session_id: str,
        system_prompt: str,
        user_message: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]] | None,
        *,
        agent_name: str | None = None,
        event_callback: Callable[[str], None] | None = None,
        log_file_path: Path | None = None,
        process_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    ) -> dict[str, Any]:
        provider_app_id = self._resolve_provider_app_id()
        provider_doc = self._get_provider_app_doc(provider_app_id)
        env = self._build_env(provider_doc, mcp_servers)

        run_cwd = cwd or "/workspace"
        prompt = self._build_prompt(system_prompt, user_message)
        prompt += "\n\nNever go in background. Always work in foreground and wait for completion of tasks.\n" + \
        "If you building some matrix/table and need yes/not or true/false in it, better use emojis for visibility.\n" + \
        "If you putting clickable link, make it link, do not wrap with '`'."
        command_parts = self._render_command(
            prompt,
            session_id=session_id,
            agent_name=agent_name,
            additional_mcp_config=mcp_servers,
        )

        result = self._run_with_live_output(
            command_parts,
            cwd=run_cwd,
            env=env,
            event_callback=event_callback,
            log_file_path=log_file_path,
            process_callback=process_callback,
        )
        if result.returncode != 0:
            raise RuntimeError(f"copilot failed with exit code {result.returncode}")

        final_output = extract_final_message(result.stdout or "")

        return {
            "session_id": session_id,
            "provider_app_id": provider_app_id,
            "output": final_output,
        }

    def _resolve_provider_app_id(self) -> str:
        settings = get_dop_app_settings("devops-pass-ai")
        provider_app_id = str(settings.get("agent.provider", "github-copilot")).strip()
        if not provider_app_id:
            raise ValueError("Missing required setting devops-pass-ai.settings['agent.provider']")
        return provider_app_id

    def _get_provider_app_doc(self, provider_app_id: str) -> DopDoc:
        database = self._get_database()
        docs = database.list_docs(doc_type="dop_app", app_id=provider_app_id)
        if len(docs) == 0:
            raise ValueError(f"Agent provider app not found for app_id '{provider_app_id}'")
        return docs[0]

    def _build_env(self, provider_doc: DopDoc, mcp_servers: list[dict[str, Any]] | None) -> dict[str, str]:
        env = os.environ.copy()

        content = provider_doc.content if isinstance(provider_doc.content, dict) else {}
        settings = content.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}

        copilot_key = settings.get("copilot-cli.key")
        if isinstance(copilot_key, str) and copilot_key.strip():
            env["COPILOT_GITHUB_TOKEN"] = copilot_key.strip()

        if mcp_servers:
            env["MCP_SERVERS"] = json.dumps(mcp_servers, ensure_ascii=False)

        return env

    def _build_prompt(self, system_prompt: str, user_message: str) -> str:
        if system_prompt and user_message:
            return f"System instructions:\n{system_prompt}\n\nUser message:\n{user_message}"
        if system_prompt:
            return system_prompt
        return user_message

    @staticmethod
    def _render_command(
        prompt: str,
        *,
        session_id: str,
        agent_name: str | None,
        additional_mcp_config: list[dict[str, Any]] | None,
    ) -> list[str]:
        # command = ["copilot", "--yolo", "--resume", session_id, "-s", "-p", prompt]
        command = ["copilot", "--yolo","--output-format", "json", "--resume", session_id, "-p", prompt]
        if agent_name:
            command.extend(["--agent", agent_name])
        if additional_mcp_config:
            command.extend(["--additional-mcp-config", json.dumps({"servers": additional_mcp_config}, ensure_ascii=False)])
        return command

    @staticmethod
    def _copilot_agents_dir() -> Path:
        return Path.home() / ".copilot" / "agents"

    @staticmethod
    def sync_custom_agent_profiles(chat_agents: list[dict[str, Any]]) -> list[Path]:
        agents_dir = AgentRunner._copilot_agents_dir()
        agents_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for agent in chat_agents:
            name = str(agent.get("name") or "").strip().lower()
            if not name:
                continue

            description = str(agent.get("description") or agent.get("title") or name).strip()
            if not description:
                description = name

            prompt = str(agent.get("prompt") or "").strip()
            if not prompt:
                continue

            model = str(agent.get("model") or "").strip()

            file_path = agents_dir / f"{name}.agent.md"
            frontmatter_lines = [
                "---",
                f"name: {name}",
                f"description: {json.dumps(description, ensure_ascii=False)}",
                "tools: ['*']",
            ]
            if model:
                frontmatter_lines.append(f"model: {json.dumps(model, ensure_ascii=False)}")
            if name == "devops_librarian":
                frontmatter_lines = [
                    "---",
                    f"name: {name}",
                    f"description: {json.dumps(description, ensure_ascii=False)}",
                    "tools: ['read', 'search', 'devops-pass-ai/list_doc_types', 'devops-pass-ai/search_docs']",
                    "mcp-servers:",
                    "  devops-pass-ai:",
                    "    type: http",
                    "    url: http://localhost:10818/mcp",
                    "    tools: ['list_doc_types', 'search_docs']",
                ]
                if model:
                    frontmatter_lines.append(f"model: {json.dumps(model, ensure_ascii=False)}")
            frontmatter_lines.append("---")
            content = "\n".join(frontmatter_lines) + f"\n\n{prompt}\n"
            file_path.write_text(content, encoding="utf-8")
            written.append(file_path)

        return written

    def sync_app_yaml_agent_profiles(self) -> list[Path]:
        database = self._get_database()
        registry = PluginRegistry(self.plugins_dir)
        app_docs = database.list_docs(doc_type="dop_app", include_facts=False)
        agents_dir = self._copilot_agents_dir()
        agents_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for app_doc in app_docs:
            app_id = str(app_doc.get("app_id") or "").strip()
            if not app_id:
                continue

            content = app_doc.content if isinstance(app_doc.content, dict) else {}
            plugin_key = str(content.get("plugin_key") or "").strip()
            if not plugin_key:
                continue

            app_config = registry.get_app_config(plugin_key)
            if app_config is None:
                continue

            raw_agents = app_config.get("agents", [])
            if not isinstance(raw_agents, list):
                continue

            settings = content.get("settings", {})
            if not isinstance(settings, dict):
                settings = {}
            substitutions = {str(key): "" if value is None else str(value) for key, value in settings.items()}
            substitutions["app_id"] = app_id

            for raw_agent in raw_agents:
                if not isinstance(raw_agent, dict):
                    continue

                source_name = str(raw_agent.get("name") or "").strip()
                if not source_name:
                    continue

                base_name = self._sanitize_agent_name(source_name)
                derived_name = self._sanitize_agent_name(f"{base_name}-{app_id}")
                if not derived_name:
                    continue

                rendered_agent = self._render_with_substitutions(raw_agent, substitutions)
                prompt = str(rendered_agent.get("prompt") or "").strip()
                if not prompt:
                    continue

                frontmatter: dict[str, Any] = {
                    key: value
                    for key, value in rendered_agent.items()
                    if key != "prompt"
                }
                frontmatter["name"] = derived_name
                description = str(frontmatter.get("description") or "").strip() or derived_name
                frontmatter["description"] = description
                # Replace "." with "_" in derived_name
                derived_name = derived_name.replace(".", "_")
                if 'mcp-servers' in frontmatter and isinstance(frontmatter['mcp-servers'], dict):
                    for server_key, server_value in frontmatter['mcp-servers'].items():
                        if isinstance(server_value, dict) and "env" in server_value and isinstance(server_value["env"], dict):
                            server_value["env"] = {key: str(value) for key, value in server_value["env"].items()}
                        if not "tools" in frontmatter['mcp-servers'][server_key] or not isinstance(frontmatter['mcp-servers'][server_key]["tools"], list):
                            frontmatter['mcp-servers'][server_key]["tools"] = ["*"]

                file_path = agents_dir / f"{derived_name}.agent.md"
                file_path.write_text(self._compose_agent_file(frontmatter, prompt), encoding="utf-8")
                written.append(file_path)

        return written

    def _get_database(self) -> Database:
        if not self.db_path.exists():
            raise ValueError(f"Database does not exist: {self.db_path}")
        return Database(db_path=self.db_path)

    @classmethod
    def _render_with_substitutions(cls, value: Any, substitutions: dict[str, str]) -> Any:
        if isinstance(value, str):
            return cls._agent_placeholder_pattern.sub(
                lambda match: substitutions.get(str(match.group(1)).strip(), ""),
                value,
            )
        if isinstance(value, list):
            return [cls._render_with_substitutions(item, substitutions) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._render_with_substitutions(item, substitutions)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _sanitize_agent_name(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower())
        normalized = re.sub(r"^[^a-z0-9]+", "", normalized)
        normalized = re.sub(r"[^a-z0-9]+$", "", normalized)
        return normalized[:63]

    @staticmethod
    def _compose_agent_file(frontmatter: dict[str, Any], prompt: str) -> str:
        frontmatter_text = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_style='"'
        ).strip()
        return f"---\n{frontmatter_text}\n---\n\n{prompt}\n"

    @staticmethod
    def _run_with_live_output(
        command_parts: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        event_callback: Callable[[str], None] | None = None,
        log_file_path: Path | None = None,
        process_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    ) -> subprocess.CompletedProcess:
        process = subprocess.Popen(
            command_parts,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if process_callback is not None:
            process_callback(process)
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        tool_calls_by_id: dict[str, dict[str, Any]] = {}

        assert process.stdout is not None
        assert process.stderr is not None

        log_fh = None
        if log_file_path is not None:
            try:
                log_file_path.parent.mkdir(parents=True, exist_ok=True)
                log_fh = log_file_path.open("a", encoding="utf-8")
            except Exception:  # noqa: BLE001
                log_fh = None

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")

        try:
            while True:
                if process.poll() is not None and not selector.get_map():
                    break

                for key, _ in selector.select(timeout=0.2):
                    stream = key.fileobj
                    line = stream.readline()
                    if not line:
                        selector.unregister(stream)
                        continue
                    if key.data == "stdout":
                        print(line, end="", flush=True)
                        stdout_lines.append(line)
                        # Write raw line to log file
                        if log_fh is not None:
                            log_fh.write(line)
                            log_fh.flush()
                        # Parse JSON event and call the activity callback
                        if event_callback is not None:
                            event = parse_copilot_event_line(line)
                            if event is not None:
                                if str(event.get("type", "")).lower() == "tool.execution_start":
                                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                                    tool_call_id = str(data.get("toolCallId", "")).strip()
                                    if tool_call_id:
                                        tool_calls_by_id[tool_call_id] = data
                                display_text = format_event_for_display(event, tool_calls_by_id=tool_calls_by_id)
                                if display_text:
                                    event_callback(display_text)
                    else:
                        print(line, end="", flush=True)
                        stderr_lines.append(line)

                if process.poll() is not None and not selector.get_map():
                    break
        finally:
            if log_fh is not None:
                log_fh.close()

        return subprocess.CompletedProcess(
            args=command_parts,
            returncode=process.returncode or 0,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
        )
