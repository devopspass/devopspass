from pathlib import Path
from typing import Any

import yaml

META_DOC_FIELDS = {
    "title",
    "description",
    "description_long",
    "filter_field",
    "source",
    "get_locally",
    "icon",
    "actions_view_type",
    "cache_ok",
    "hidden_fields",
    "actions",
}


class PluginRegistry:
    def __init__(self, plugins_dir: Path) -> None:
        self.plugins_dir = plugins_dir

    def list_app_configs(self) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []

        if not self.plugins_dir.exists():
            return configs

        for app_yaml in sorted(self.plugins_dir.glob("*/app.yaml")):
            with app_yaml.open("r", encoding="utf-8") as file:
                raw = yaml.safe_load(file) or {}

            plugin_key = app_yaml.parent.name
            doc_types = self._parse_doc_types(raw.get("doc_types", []))
            settings = raw.get("settings", {}) or {}
            agents = raw.get("agents", []) or []
            normalized_agents = [agent for agent in agents if isinstance(agent, dict)] if isinstance(agents, list) else []

            normalized_settings: dict[str, Any] = {}
            for key, definition in settings.items():
                definition = definition or {}
                normalized_settings[key] = {
                    "title": definition.get("title", key),
                    "description": definition.get("description", ""),
                    "mandatory": bool(definition.get("mandatory", False)),
                    "type": definition.get("type", "string"),
                    "default": definition.get("default"),
                    "options": definition.get("options", []),
                }

            configs.append(
                {
                    "plugin_key": plugin_key,
                    "name": raw.get("name", plugin_key),
                    "description": raw.get("description", ""),
                    "description_long": raw.get("description_long", ""),
                    "icon": raw.get("icon"),
                    "uniq": bool(raw.get("uniq", False)),
                    "app_id": raw.get("app_id"),
                    "doc_types": doc_types,
                    "settings": normalized_settings,
                    "agents": normalized_agents,
                    "category": raw.get("category"),
                    "check_script": raw.get("check_script"),
                    "agent_provider": bool(raw.get("agent_provider", False)),
                }
            )

        return configs

    def get_app_config(self, plugin_key: str) -> dict[str, Any] | None:
        for app in self.list_app_configs():
            if app["plugin_key"] == plugin_key:
                return app
        return None

    @staticmethod
    def _parse_doc_types(raw_doc_types: Any) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []

        if not isinstance(raw_doc_types, list):
            return parsed

        for item in raw_doc_types:
            if isinstance(item, str):
                parsed.append({"key": item, "title": item})
                continue

            if not isinstance(item, dict):
                continue

            doc_type_key: str | None = None
            doc_type_meta: dict[str, Any] = {}

            for key, value in item.items():
                if key not in META_DOC_FIELDS and doc_type_key is None:
                    doc_type_key = key
                    if isinstance(value, dict):
                        doc_type_meta.update(value)
                else:
                    doc_type_meta[key] = value

            if doc_type_key is None:
                continue

            doc_type_meta.setdefault("title", doc_type_key)
            parsed.append({"key": doc_type_key, **doc_type_meta})

        return parsed
