from __future__ import annotations

import os
import subprocess
from typing import Any

import dop


def do_test(dop_app: Any) -> dict[str, Any] | dop.DopError:
    token = str(dop_app.settings.get("copilot-cli.key", "")).strip()
    if not token:
        return dop.error("copilot-cli.key is required")

    command_candidates = [
        ["copilot", "-p", "Are you here? Reply instantly!"],
    ]
    last_output = ""

    for command in command_candidates:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "COPILOT_GITHUB_TOKEN": token,
            },
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            return dop.error(f"GitHub Copilot CLI test command failed: {output[:300]}")
        if 'No authentication information found.' not in result.stderr:
            return {"status": "success", "message": output or "GitHub Copilot CLI authentication passed"}
        last_output = output

    if "No such file or directory" in last_output or "not found" in last_output.lower():
        return dop.error("copilot-cli executable not found in PATH")

    return dop.error(last_output or "GitHub Copilot CLI authentication failed")
