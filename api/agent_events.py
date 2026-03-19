"""Utility functions for parsing and formatting GitHub Copilot CLI JSON event output.

The copilot CLI with --output-format json outputs one JSON object per line (JSONL).
Each object represents a step in the agent's work.
"""

from __future__ import annotations

import json
from typing import Any


_TOOL_ICONS: dict[str, str] = {
    "read_file": "📄",
    "write_file": "✏️",
    "edit_file": "✏️",
    "create_file": "✏️",
    "replace_string_in_file": "✏️",
    "multi_replace_string_in_file": "✏️",
    "list_directory": "📁",
    "list_dir": "📁",
    "run_in_terminal": "💻",
    "run_command": "💻",
    "search": "🔍",
    "grep_search": "🔍",
    "file_search": "🔍",
    "semantic_search": "🔍",
    "tool_search_tool_regex": "🔍",
    "get_errors": "⚠️",
    "fetch_webpage": "🌐",
    "open_browser_page": "🌐",
    "get_terminal_output": "💻",
    "manage_todo_list": "📋",
    "memory": "🧠",
    "runSubagent": "🤖",
    "search_subagent": "🤖",
}

_TOOL_LABELS: dict[str, str] = {
    "read_file": "Reading file",
    "write_file": "Writing file",
    "edit_file": "Editing file",
    "create_file": "Creating file",
    "replace_string_in_file": "Editing file",
    "multi_replace_string_in_file": "Editing files",
    "list_directory": "Listing directory",
    "list_dir": "Listing directory",
    "run_in_terminal": "Running command",
    "run_command": "Running command",
    "search": "Searching",
    "grep_search": "Searching code",
    "file_search": "Finding file",
    "semantic_search": "Semantic search",
    "tool_search_tool_regex": "Finding tools",
    "get_errors": "Checking errors",
    "fetch_webpage": "Fetching page",
    "open_browser_page": "Opening page",
    "get_terminal_output": "Reading terminal",
    "manage_todo_list": "Updating tasks",
    "memory": "Accessing memory",
    "runSubagent": "Running subagent",
    "search_subagent": "Running search agent",
}


def parse_copilot_event_line(line: str) -> dict[str, Any] | None:
    """Parse one line from copilot --output-format json output.

    Returns a structured event dict or None if the line should be ignored.
    """
    line = line.strip()
    if not line:
        return None

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        # Non-JSON line: treat as plain text activity
        return {"type": "raw", "text": line}

    if not isinstance(event, dict):
        return None

    return event


def format_event_for_display(
    event: dict[str, Any],
    *,
    tool_calls_by_id: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """Convert a parsed copilot event dict to a human-readable label.

    Returns None if the event should be silently skipped.
    """
    if not isinstance(event, dict):
        return None

    event_type = str(event.get("type", "")).lower()

    # Skip noisy protocol/meta events.
    if event_type.startswith("session.") or event_type == "result":
        return None
    if event_type == "user.message":
        return None

    # ── assistant / final message ────────────────────────────────────────────
    if event_type in ("message", "assistantmessage", "assistant_message", "assistant.message"):
        role = str(event.get("role", "assistant")).lower()
        if role in ("", "assistant"):
            content = _extract_text_content(event)
            if content:
                snippet = content[:160].replace("\n", " ")
                if len(content) > 160:
                    snippet += "…"
                return f"💬 {snippet}"
        return None

    # Deltas are token-level and too noisy for end users.
    if event_type.endswith(".message_delta") or event_type.endswith(".reasoning_delta"):
        return None

    if event_type.endswith(".turn_start"):
        return "🤖 Working…"

    if event_type.endswith(".turn_end"):
        return None

    if event_type == "tool.execution_start":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        tool_name = str(data.get("toolName", "")).strip() or _extract_tool_name(event)
        arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        detail = _tool_detail(tool_name, arguments) if isinstance(arguments, dict) else ""
        if detail:
            return f"{_TOOL_ICONS.get(tool_name, '🔧')} {_TOOL_LABELS.get(tool_name, tool_name)}: {detail}"
        return f"{_TOOL_ICONS.get(tool_name, '🔧')} {_TOOL_LABELS.get(tool_name, tool_name)}"

    if event_type == "tool.execution_complete":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        tool_call_id = str(data.get("toolCallId", "")).strip()
        tool_info = tool_calls_by_id.get(tool_call_id, {}) if tool_calls_by_id is not None else {}
        tool_name = str(tool_info.get("toolName") or data.get("toolName") or _extract_tool_name(event)).strip() or "tool"
        arguments = tool_info.get("arguments") if isinstance(tool_info.get("arguments"), dict) else {}
        detail = _tool_detail(tool_name, arguments) if isinstance(arguments, dict) else ""
        prefix = "✓" if bool(data.get("success", True)) else "✗"
        if detail:
            return f"{prefix} Completed: {_TOOL_LABELS.get(tool_name, tool_name)}: {detail}"
        return f"{prefix} Completed: {_TOOL_LABELS.get(tool_name, tool_name)}"

    # ── thinking / planning ──────────────────────────────────────────────────
    if event_type in ("thinking", "agentthinking", "agent_thinking", "plan", "reasoning", "assistant.reasoning"):
        content = _extract_text_content(event)
        if content:
            snippet = content[:200].replace("\n", " ")
            if len(content) > 200:
                snippet += "…"
            return f"🤔 {snippet}"
        return "🤔 Planning…"

    # ── tool / function call ─────────────────────────────────────────────────
    if event_type in (
        "tool_call", "toolcall", "function_call", "action", "tool_invocation",
        "tool_use", "tooluse", "assistant.tool_call",
    ):
        name = _extract_tool_name(event)
        status = str(event.get("status", "")).lower()

        if status in ("completed", "success", "done"):
            icon = _TOOL_ICONS.get(name, "✓")
            return f"{icon} Done: {_TOOL_LABELS.get(name, name)}"

        if status in ("failed", "error"):
            icon = _TOOL_ICONS.get(name, "✗")
            return f"{icon} Failed: {_TOOL_LABELS.get(name, name)}"

        # started / invoking
        args = event.get("arguments") or event.get("parameters") or event.get("input") or {}
        if not args and isinstance(event.get("data"), dict):
            args = event["data"].get("arguments") or event["data"].get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}

        detail = _tool_detail(name, args) if isinstance(args, dict) else ""
        icon = _TOOL_ICONS.get(name, "🔧")
        label = _TOOL_LABELS.get(name, name)
        if detail:
            return f"{icon} {label}: {detail}"
        return f"{icon} {label}"

    # ── tool result ──────────────────────────────────────────────────────────
    if event_type in (
        "tool_result", "toolresult", "function_call_output", "action_result",
        "tool_output", "assistant.tool_result",
    ):
        name = _extract_tool_name(event)
        status = str(event.get("status", "success")).lower()
        icon = "✓" if status in ("success", "ok", "") else "✗"
        label = _TOOL_LABELS.get(name, name) if name and name != "unknown" else "tool"
        return f"{icon} Completed: {label}"

    # ── error ────────────────────────────────────────────────────────────────
    if event_type in ("error", "failure"):
        msg = _extract_text_content(event) or str(event.get("message", "Unknown error"))
        return f"❌ Error: {msg[:200]}"

    # ── progress / status ────────────────────────────────────────────────────
    if event_type in ("progress", "status", "info", "log"):
        content = _extract_text_content(event)
        if content:
            return f"ℹ️ {content[:200]}"
        return None

    # ── raw (non-JSON line) ──────────────────────────────────────────────────
    if event_type == "raw":
        text = str(event.get("text", "")).strip()
        if text:
            return text
        return None

    # ── fallback: show type + brief content ──────────────────────────────────
    content = _extract_text_content(event)
    display_type = str(event.get("type", "event"))
    if content:
        snippet = content[:150].replace("\n", " ")
        return f"[{display_type}] {snippet}"

    return None


def extract_final_message(stdout: str) -> str:
    """Extract the final assistant response text from copilot --output-format json stdout.

    Falls back to non-JSON lines if no structured message events are found.
    """
    message_parts: list[str] = []
    delta_parts_by_message_id: dict[str, list[str]] = {}
    delta_message_order: list[str] = []
    plain_lines: list[str] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                plain_lines.append(line)
                continue

            event_type = str(event.get("type", "")).lower()
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            role = str(event.get("role", "")).lower()
            if not role and event_type.startswith("assistant."):
                role = "assistant"

            if event_type in (
                "message", "assistantmessage", "assistant_message", "text", "assistant.message",
            ) and role in ("", "assistant"):
                content = _extract_text_content(event)
                if content:
                    message_parts.append(content)

            if event_type == "assistant.message_delta":
                message_id = str(data.get("messageId", "")).strip()
                delta = str(data.get("deltaContent", ""))
                if message_id and delta:
                    if message_id not in delta_parts_by_message_id:
                        delta_parts_by_message_id[message_id] = []
                        delta_message_order.append(message_id)
                    delta_parts_by_message_id[message_id].append(delta)

        except json.JSONDecodeError:
            plain_lines.append(line)

    if message_parts:
        # Prefer the most recent complete assistant message.
        return message_parts[-1].strip()

    # Fallback: reassemble from message deltas when full message event is missing.
    if delta_message_order:
        last_id = delta_message_order[-1]
        assembled = "".join(delta_parts_by_message_id.get(last_id, [])).strip()
        if assembled:
            return assembled

    # Nothing matched — return non-JSON lines or raw stdout
    if plain_lines:
        return "\n".join(plain_lines).strip()

    return stdout.strip()


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_text_content(event: dict[str, Any]) -> str:
    """Extract text/content from an event dict."""
    for key in ("content", "text", "message", "thinking", "output", "data", "result"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            # Prefer content-like keys in nested payloads before generic recursion.
            for nested_key in ("content", "text", "deltaContent", "message", "reasoningText", "reasoning"):
                nested_val = val.get(nested_key)
                if isinstance(nested_val, str) and nested_val.strip():
                    return nested_val.strip()
            nested = _extract_text_content(val)
            if nested:
                return nested
    return ""


def _extract_tool_name(event: dict[str, Any]) -> str:
    """Extract tool/function name from an event dict."""
    for key in ("name", "tool", "tool_name", "function_name", "action_name"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    fn = event.get("function")
    if isinstance(fn, dict):
        name = fn.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("toolName", "name", "tool", "function_name"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return "unknown"


def _tool_detail(name: str, args: dict[str, Any]) -> str:
    """Create a brief detail string for a tool call."""
    if not isinstance(args, dict):
        return ""

    if name in ("read_file", "write_file", "edit_file", "create_file",
                "replace_string_in_file"):
        path = args.get("path") or args.get("filePath") or args.get("file_path", "")
        if path:
            return str(path)

    if name in ("run_in_terminal", "run_command"):
        cmd = args.get("command", "")
        if cmd:
            return str(cmd)[:80]

    if name in ("grep_search", "search", "file_search", "tool_search_tool_regex"):
        q = args.get("query") or args.get("q") or args.get("pattern", "")
        if q:
            return f'"{str(q)[:60]}"'

    if name in ("list_directory", "list_dir"):
        path = args.get("path", "")
        if path:
            return str(path)

    if name in ("fetch_webpage", "open_browser_page"):
        url = args.get("url", "")
        if url:
            return str(url)[:80]

    if name == "semantic_search":
        q = args.get("query", "")
        if q:
            return f'"{str(q)[:60]}"'

    if name == "multi_replace_string_in_file":
        reps = args.get("replacements", [])
        if isinstance(reps, list) and reps:
            paths = {r.get("filePath", "") for r in reps if isinstance(r, dict)}
            paths.discard("")
            if paths:
                return ", ".join(sorted(paths)[:3])

    if name == "runSubagent":
        desc = args.get("description", "")
        if desc:
            return str(desc)[:60]

    if args:
        compact = json.dumps(_truncate_for_display(args), ensure_ascii=False, sort_keys=True)
        if len(compact) > 180:
            compact = compact[:177] + "..."
        return compact

    return ""


def _truncate_for_display(value: Any, *, max_items: int = 5, max_string: int = 80) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_string else value[: max_string - 3] + "..."
    if isinstance(value, list):
        items = [_truncate_for_display(item, max_items=max_items, max_string=max_string) for item in value[:max_items]]
        if len(value) > max_items:
            items.append("...")
        return items
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["..."] = "..."
                break
            result[str(key)] = _truncate_for_display(item, max_items=max_items, max_string=max_string)
        return result
    return value
