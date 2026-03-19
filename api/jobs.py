import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, local
from typing import Any, Callable

import dop
from db import Database


JobStatus = str
_thread_runtime = local()
ASKPASS_CANCELLED_MARKER = "__DOP_ASKPASS_CANCELLED__"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobLogWriter(io.TextIOBase):
    def __init__(self, write_entry: Callable[[str], None]) -> None:
        self._buffer = ""
        self._write_entry = write_entry

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0

        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._write_entry(line)
        return len(s)

    def flush(self) -> None:
        if self._buffer:
            self._write_entry(self._buffer)
            self._buffer = ""


@dataclass
class RuntimeApplicationDoc:
    id: int
    app_id: str | None
    doc_type: str
    settings: dict[str, Any]
    content: dict[str, Any]


def run_command(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """
    Run a subprocess command and capture output for job logs.

    Automatically pipes stdout/stderr and prints to current stream
    (which is redirected to job logs by JobLogWriter).

    Args:
        args: Command arguments (list)
        **kwargs: Other subprocess.run arguments (check=True, cwd, env, etc.)

    Returns:
        subprocess.CompletedProcess instance

    Raises:
        subprocess.CalledProcessError: If check=True and command fails (after printing output)
    """
    runtime_env = getattr(_thread_runtime, "command_env", None)
    explicit_env = kwargs.pop("env", None)
    if runtime_env is not None or explicit_env is not None:
        merged_env = os.environ.copy()
        if runtime_env is not None:
            merged_env.update(runtime_env)
        if explicit_env is not None:
            merged_env.update(explicit_env)
        kwargs["env"] = merged_env

    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs
        )
        # Print output on success
        if result.stdout:
            print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='')
        return result
    except subprocess.CalledProcessError as e:
        # Print output even on failure, then re-raise
        if e.stdout:
            print(e.stdout, end='')
        if e.stderr:
            print(e.stderr, end='')
        raise


class JobsManager:
    def __init__(
        self,
        database: Database,
        get_registry: Callable[[], Any],
        plugins_dir: Path,
        data_dir: Path,
        logs_dir: Path | None = None,
        jobs_retention_days: int = 7,
        jobs_list_limit: int = 100,
        workflow_max_parallel_default: int = 3,
    ) -> None:
        self.database = database
        self.get_registry = get_registry
        self.plugins_dir = plugins_dir
        self.data_dir = data_dir
        self.logs_dir = logs_dir
        self.jobs_retention_days = max(1, int(jobs_retention_days))
        self.jobs_list_limit = max(1, int(jobs_list_limit))
        self.workflow_max_parallel_default = max(1, int(workflow_max_parallel_default))
        if self.logs_dir is not None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_processes: dict[str, subprocess.Popen[str]] = {}
        self._askpass_requests: dict[str, dict[str, Any]] = {}  # request_id -> request payload
        self._askpass_saved: dict[str, str] = {}
        self._lock = Lock()
        self._mark_incomplete_jobs_failed_on_startup()
        self._prune_old_jobs()
        self._load_jobs_from_db()

    @staticmethod
    def _askpass_cache_key(prompt: str) -> str:
        return " ".join(prompt.split())

    def _load_jobs_from_db(self) -> None:
        persisted_jobs = self.database.list_jobs(limit=self.jobs_list_limit)
        with self._lock:
            self._jobs = {}
            for job in persisted_jobs:
                self._normalize_job_dependency_fields(job)
                self._jobs[job["id"]] = job

    def _normalize_job_dependency_fields(self, job: dict[str, Any]) -> None:
        depends_on = job.get("depends_on_job_ids")
        if not isinstance(depends_on, list):
            depends_on = []
        job["depends_on_job_ids"] = [str(item) for item in depends_on if str(item).strip()]

        dependent_ids = job.get("dependent_job_ids")
        if not isinstance(dependent_ids, list):
            dependent_ids = []
        job["dependent_job_ids"] = [str(item) for item in dependent_ids if str(item).strip()]

        workflow_id = job.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            workflow_id = str(uuid.uuid4())
        job["workflow_id"] = workflow_id

        raw_max_parallel = job.get("workflow_max_parallel")
        try:
            workflow_max_parallel = int(raw_max_parallel)
        except (TypeError, ValueError):
            workflow_max_parallel = self.workflow_max_parallel_default
        job["workflow_max_parallel"] = max(1, workflow_max_parallel)

        blocking_reason = job.get("blocking_reason")
        if not isinstance(blocking_reason, str):
            blocking_reason = None
        job["blocking_reason"] = blocking_reason

    def _persist_job(self, job: dict[str, Any]) -> None:
        self.database.upsert_job(job)

    def _mark_incomplete_jobs_failed_on_startup(self) -> None:
        summary = "Marked as failed after API restart while job was in progress"
        self.database.mark_incomplete_jobs_failed(summary)

    def _prune_old_jobs(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.jobs_retention_days)).isoformat()
        self.database.delete_old_jobs(cutoff)
        with self._lock:
            stale_ids = [job_id for job_id, job in self._jobs.items() if str(job.get("created_at") or "") < cutoff]
            for job_id in stale_ids:
                self._jobs.pop(job_id, None)

    def _hydrate_job_details(self, job: dict[str, Any]) -> None:
        logs = self.database.list_job_logs(str(job["id"]))
        agent_events = self.database.list_job_agent_events(str(job["id"]))
        with self._lock:
            existing = self._jobs.get(str(job["id"]))
            if existing is None:
                return
            existing["logs"] = logs
            existing["agent_events"] = agent_events

    @staticmethod
    def _askpass_prompt_kind(prompt: str) -> str:
        normalized = prompt.lower()
        if "username for" in normalized:
            return "username"
        if "passphrase" in normalized:
            return "ssh_passphrase"
        return "password"

    @staticmethod
    def _extract_ssh_key_path_from_prompt(prompt: str) -> str | None:
        quoted_match = re.search(r"passphrase for key ['\"]([^'\"]+)['\"]", prompt, flags=re.IGNORECASE)
        if quoted_match:
            return quoted_match.group(1)

        unquoted_match = re.search(r"passphrase for key\s+([^:]+):", prompt, flags=re.IGNORECASE)
        if unquoted_match:
            return unquoted_match.group(1).strip()

        return None

    @staticmethod
    def _default_ssh_key_candidates() -> list[Path]:
        ssh_dir = Path.home() / ".ssh"
        return [
            ssh_dir / "id_ed25519",
            ssh_dir / "id_ecdsa",
            ssh_dir / "id_rsa",
            ssh_dir / "id_dsa",
        ]

    @staticmethod
    def _run_ssh_add_with_passphrase(key_path: Path, passphrase: str) -> tuple[bool, str]:
        if shutil.which("ssh-add") is None:
            return False, "ssh-add binary is not available in the API container"

        ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK", "").strip()
        if not ssh_auth_sock:
            return False, "SSH_AUTH_SOCK is not configured; ssh-agent is not available"

        askpass_script_path = ""
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", prefix="dop-ssh-askpass-", delete=False) as script_file:
                askpass_script_path = script_file.name
                script_file.write("#!/bin/sh\n")
                script_file.write("printf '%s\\n' \"$DOP_SSH_KEY_PASSPHRASE\"\n")

            os.chmod(askpass_script_path, 0o700)

            env = os.environ.copy()
            env["SSH_ASKPASS"] = askpass_script_path
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env["DOP_SSH_KEY_PASSPHRASE"] = passphrase
            env.setdefault("DISPLAY", ":0")

            result = subprocess.run(
                ["ssh-add", str(key_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            combined = "\n".join(part.strip() for part in [result.stdout, result.stderr] if part.strip())
            if result.returncode == 0:
                return True, combined

            return False, combined or f"ssh-add exited with status {result.returncode}"
        except Exception as error:  # noqa: BLE001
            return False, f"Failed to run ssh-add: {error}"
        finally:
            if askpass_script_path:
                with contextlib.suppress(OSError):
                    os.remove(askpass_script_path)

    def _save_ssh_passphrase_to_agent(self, prompt: str, passphrase: str) -> None:
        requested_key_path = self._extract_ssh_key_path_from_prompt(prompt)
        if requested_key_path:
            candidates = [Path(requested_key_path)]
        else:
            # Generic prompts do not include key path, so try common SSH key names.
            candidates = self._default_ssh_key_candidates()

        existing_candidates = [candidate for candidate in candidates if candidate.exists()]
        if not existing_candidates:
            if requested_key_path:
                raise ValueError(f"SSH key not found: {requested_key_path}")
            raise ValueError("No default SSH keys found under ~/.ssh to save passphrase for session")

        last_error = ""
        for candidate in existing_candidates:
            ok, message = self._run_ssh_add_with_passphrase(candidate, passphrase)
            if ok:
                return
            last_error = message

        if requested_key_path:
            raise ValueError(f"Failed to add SSH key to ssh-agent: {last_error or requested_key_path}")
        raise ValueError(f"Failed to add any default SSH key to ssh-agent: {last_error or 'unknown error'}")

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [self._serialize_job(job, include_logs=False) for job in self._jobs.values()]
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return jobs[: self.jobs_list_limit]

    def _load_job_record(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            existing = self._jobs.get(job_id)
        if existing is not None:
            return existing

        job = self.database.get_job(job_id)
        if job is None:
            return None
        self._normalize_job_dependency_fields(job)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def _collect_dependency_jobs(self, depends_on_job_ids: list[str]) -> list[dict[str, Any]]:
        deps: list[dict[str, Any]] = []
        seen: set[str] = set()
        for dep_id in depends_on_job_ids:
            dep_id_normalized = str(dep_id).strip()
            if not dep_id_normalized or dep_id_normalized in seen:
                continue
            dep = self._load_job_record(dep_id_normalized)
            if dep is None:
                raise ValueError(f"Dependency job {dep_id_normalized} not found")
            seen.add(dep_id_normalized)
            deps.append(dep)
        return deps

    @staticmethod
    def _has_path_to_target(start_job: dict[str, Any], target_job_id: str, jobs_by_id: dict[str, dict[str, Any]]) -> bool:
        visited: set[str] = set()
        stack: list[str] = [str(start_job.get("id"))]
        while stack:
            current_id = stack.pop()
            if current_id in visited:
                continue
            visited.add(current_id)
            if current_id == target_job_id:
                return True
            current = jobs_by_id.get(current_id)
            if current is None:
                continue
            for parent_id in current.get("depends_on_job_ids", []):
                parent_id_str = str(parent_id)
                if parent_id_str not in visited:
                    stack.append(parent_id_str)
        return False

    def _resolve_workflow_values(
        self,
        workflow_id: str | None,
        max_parallel: int | None,
        dependency_jobs: list[dict[str, Any]],
    ) -> tuple[str, int]:
        dependency_workflow_ids = {
            str(dep.get("workflow_id"))
            for dep in dependency_jobs
            if isinstance(dep.get("workflow_id"), str) and str(dep.get("workflow_id")).strip()
        }
        if len(dependency_workflow_ids) > 1:
            raise ValueError("Dependencies must belong to a single workflow")

        dependency_workflow_id = next(iter(dependency_workflow_ids), None)
        dependency_limit = None
        if dependency_jobs:
            first_dep_limit = dependency_jobs[0].get("workflow_max_parallel")
            try:
                dependency_limit = max(1, int(first_dep_limit))
            except (TypeError, ValueError):
                dependency_limit = None

        requested_workflow_id = str(workflow_id).strip() if isinstance(workflow_id, str) and workflow_id.strip() else None
        if requested_workflow_id is not None and dependency_workflow_id is not None and requested_workflow_id != dependency_workflow_id:
            raise ValueError("Cross-workflow dependencies are not allowed")

        resolved_workflow_id = requested_workflow_id or dependency_workflow_id or str(uuid.uuid4())

        requested_limit = None
        if max_parallel is not None:
            try:
                requested_limit = max(1, int(max_parallel))
            except (TypeError, ValueError) as error:
                raise ValueError("max_parallel must be a positive integer") from error

        if dependency_limit is not None and requested_limit is not None and requested_limit != dependency_limit:
            raise ValueError(
                f"Workflow {resolved_workflow_id} already uses max_parallel={dependency_limit}; requested {requested_limit}"
            )

        resolved_limit = requested_limit or dependency_limit or self.workflow_max_parallel_default
        return resolved_workflow_id, max(1, int(resolved_limit))

    def _validate_no_dependency_cycle(self, new_job_id: str, dependency_jobs: list[dict[str, Any]]) -> None:
        with self._lock:
            jobs_by_id = {str(job["id"]): job for job in self._jobs.values()}
        for dep in dependency_jobs:
            dep_id = str(dep.get("id"))
            if dep_id == new_job_id:
                raise ValueError("A job cannot depend on itself")
            if self._has_path_to_target(dep, new_job_id, jobs_by_id):
                raise ValueError("Dependency cycle detected")

    def _link_dependencies(self, job: dict[str, Any], dependency_jobs: list[dict[str, Any]]) -> None:
        with self._lock:
            for dep in dependency_jobs:
                dep_in_store = self._jobs.get(str(dep["id"]))
                if dep_in_store is None:
                    continue
                dependent_ids = dep_in_store.setdefault("dependent_job_ids", [])
                if job["id"] not in dependent_ids:
                    dependent_ids.append(job["id"])
                    self._persist_job(dep_in_store)

    def _start_job_task(self, job_id: str) -> None:
        import asyncio

        asyncio.create_task(self._run_job(job_id))

    def _schedule_ready_jobs(self) -> None:
        to_start: list[str] = []
        with self._lock:
            for _ in range(len(self._jobs) + 1):
                changed = False
                running_by_workflow: dict[str, int] = {}
                for existing in self._jobs.values():
                    if existing.get("status") == "running":
                        workflow_id = str(existing.get("workflow_id") or "")
                        if workflow_id:
                            running_by_workflow[workflow_id] = running_by_workflow.get(workflow_id, 0) + 1

                jobs_ordered = sorted(self._jobs.values(), key=lambda item: str(item.get("created_at") or ""))
                for job in jobs_ordered:
                    status = str(job.get("status") or "")
                    if status in {"running", "success", "failed", "cancelled"}:
                        continue

                    dependency_ids = [str(dep_id) for dep_id in job.get("depends_on_job_ids", []) if str(dep_id).strip()]
                    failed_dep_id = None
                    pending_dep_ids: list[str] = []
                    for dep_id in dependency_ids:
                        dep = self._jobs.get(dep_id)
                        if dep is None:
                            pending_dep_ids.append(dep_id)
                            continue
                        dep_status = str(dep.get("status") or "")
                        if dep_status in {"failed", "cancelled"}:
                            failed_dep_id = dep_id
                            break
                        if dep_status != "success":
                            pending_dep_ids.append(dep_id)

                    if failed_dep_id is not None:
                        job["status"] = "failed"
                        job["finished_at"] = utc_now_iso()
                        message = f"Blocked by failed dependency {failed_dep_id}"
                        job["summary"] = message
                        job["failure"] = message
                        job["blocking_reason"] = message
                        self._persist_job(job)
                        changed = True
                        continue

                    if pending_dep_ids:
                        reason = f"Waiting for dependencies: {', '.join(pending_dep_ids)}"
                        if status != "blocked" or job.get("blocking_reason") != reason:
                            job["status"] = "blocked"
                            job["blocking_reason"] = reason
                            self._persist_job(job)
                            changed = True
                        continue

                    workflow_id = str(job.get("workflow_id") or "")
                    workflow_limit = max(1, int(job.get("workflow_max_parallel") or self.workflow_max_parallel_default))
                    running = running_by_workflow.get(workflow_id, 0)
                    if running >= workflow_limit:
                        reason = f"Waiting for workflow slot ({running}/{workflow_limit})"
                        if status != "blocked" or job.get("blocking_reason") != reason:
                            job["status"] = "blocked"
                            job["blocking_reason"] = reason
                            self._persist_job(job)
                            changed = True
                        continue

                    job["status"] = "running"
                    if job.get("started_at") is None:
                        job["started_at"] = utc_now_iso()
                    job["blocking_reason"] = None
                    self._persist_job(job)
                    running_by_workflow[workflow_id] = running + 1
                    to_start.append(str(job["id"]))
                    changed = True

                if not changed:
                    break

        for job_id in to_start:
            self._start_job_task(job_id)

    def _fail_pending_dependents(self, root_job_id: str, reason: str) -> None:
        queue = [root_job_id]
        seen: set[str] = set()
        with self._lock:
            while queue:
                current = queue.pop(0)
                if current in seen:
                    continue
                seen.add(current)
                current_job = self._jobs.get(current)
                if current_job is None:
                    continue

                dependent_ids = [str(dep_id) for dep_id in current_job.get("dependent_job_ids", [])]
                for dep_id in dependent_ids:
                    child = self._jobs.get(dep_id)
                    if child is None:
                        continue
                    child_status = str(child.get("status") or "")
                    if child_status in {"success", "failed", "cancelled", "running"}:
                        continue
                    child["status"] = "failed"
                    child["finished_at"] = utc_now_iso()
                    child["summary"] = reason
                    child["failure"] = reason
                    child["blocking_reason"] = reason
                    self._persist_job(child)
                    queue.append(dep_id)

    def create_askpass_request(self, job_id: str, prompt: str) -> dict[str, Any]:
        """Create a password request that the UI should handle."""
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError(f"Job {job_id} not found")
            cache_key = self._askpass_cache_key(prompt)
            saved_answer = self._askpass_saved.get(cache_key)
            prompt_kind = self._askpass_prompt_kind(prompt)

            request_id = str(uuid.uuid4())
            self._askpass_requests[request_id] = {
                "job_id": job_id,
                "prompt": prompt,
                "answer": saved_answer,
                "cache_key": cache_key,
                "prompt_kind": prompt_kind,
                "created_at": utc_now_iso(),
            }
            return {
                "request_id": request_id,
                "job_id": job_id,
                "prompt": prompt,
                "prompt_kind": prompt_kind,
                "can_save": prompt_kind != "username",
            }

    def answer_askpass_request(self, request_id: str, password: str, save: bool = False) -> bool:
        """Set the password answer for an askpass request."""
        with self._lock:
            if request_id not in self._askpass_requests:
                return False
            request = self._askpass_requests[request_id]

            if save:
                prompt_kind = str(request.get("prompt_kind") or "password")
                if prompt_kind == "ssh_passphrase":
                    prompt = str(request.get("prompt") or "")
                    self._save_ssh_passphrase_to_agent(prompt, password)

                cache_key = request.get("cache_key")
                if isinstance(cache_key, str) and cache_key:
                    self._askpass_saved[cache_key] = password

            request["answer"] = password
            return True

    def cancel_askpass_request(self, request_id: str) -> bool:
        """Cancel an askpass request so the waiting git process can fail fast."""
        with self._lock:
            if request_id not in self._askpass_requests:
                return False
            self._askpass_requests[request_id]["answer"] = ASKPASS_CANCELLED_MARKER
            return True

    def get_askpass_answer(self, request_id: str) -> str | None:
        """
        Return the current answer for an askpass request without blocking.
        If answered, consume the request and return the password once.
        """
        with self._lock:
            request = self._askpass_requests.get(request_id)
            if request is None:
                return None

            answer = request.get("answer")
            if answer is None:
                return None

            self._askpass_requests.pop(request_id, None)
            return str(answer)

    def get_pending_askpass_requests(self, job_id: str) -> list[dict[str, Any]]:
        """Get all pending (unanswered) askpass requests for a job."""
        with self._lock:
            requests = []
            for request_id, req_data in self._askpass_requests.items():
                if req_data.get("job_id") == job_id and req_data.get("answer") is None:
                    requests.append({
                        "request_id": request_id,
                        "job_id": job_id,
                        "prompt": req_data.get("prompt"),
                        "prompt_kind": req_data.get("prompt_kind", "password"),
                        "can_save": req_data.get("prompt_kind", "password") != "username",
                    })
            return requests

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)

        if job is None:
            job = self.database.get_job(job_id)
            if job is None:
                return None
            self._normalize_job_dependency_fields(job)
            with self._lock:
                self._jobs[job_id] = job

        self._hydrate_job_details(job)
        return self._serialize_job(job, include_logs=True)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        cancelled_while_queued = False
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            if not job.get("can_cancel"):
                raise ValueError(f"Job {job_id} cannot be cancelled")
            if job["status"] in ("success", "failed", "cancelled"):
                return self._serialize_job(job, include_logs=True)

            job["cancel_requested"] = True
            self._persist_job(job)
            process = self._job_processes.get(job_id)

            if job["status"] in ("queued", "blocked"):
                job["status"] = "cancelled"
                job["finished_at"] = utc_now_iso()
                job["summary"] = "Cancelled by user"
                job["blocking_reason"] = "Cancelled by user"
                self._persist_job(job)
                cancelled_while_queued = True

        if cancelled_while_queued:
            self._fail_pending_dependents(job_id, f"Blocked by cancelled dependency {job_id}")
            self._append_agent_event(job_id, "⏹ Cancelled by user")
            self._schedule_ready_jobs()
            return self.get_job(job_id) or {}

        self._append_log(job_id, "stderr", "Job cancellation requested by user")
        self._append_agent_event(job_id, "⏹ Stopping job…")

        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

        return self.get_job(job_id) or {}

    async def create_docs_refresh_job(
        self,
        app_doc_id: int,
        doc_type: str,
        depends_on_job_ids: list[str] | None = None,
        workflow_id: str | None = None,
        max_parallel: int | None = None,
    ) -> dict[str, Any]:
        app_doc = self.database.get_doc(app_doc_id)
        if app_doc.get("doc_type") != "dop_app":
            raise ValueError(f"Application {app_doc_id} not found")

        content = app_doc.get("content", {})
        plugin_key = content.get("plugin_key")
        if not isinstance(plugin_key, str) or not plugin_key.strip():
            raise ValueError("Application plugin_key is not configured")

        registry = self.get_registry()
        app_config = registry.get_app_config(plugin_key)
        if app_config is None:
            raise ValueError(f"Unknown plugin app: {plugin_key}")

        doc_type_entry = self._find_doc_type_entry(app_config, doc_type)
        if doc_type_entry is None:
            raise ValueError(f"Doc type {doc_type} not found for plugin {plugin_key}")

        source = doc_type_entry.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"Doc type {doc_type} does not define source")

        source_path = (self.plugins_dir / source).resolve()
        if not source_path.exists():
            raise ValueError(f"Plugin source not found: {source}")

        dependencies = self._collect_dependency_jobs(depends_on_job_ids or [])
        resolved_workflow_id, resolved_max_parallel = self._resolve_workflow_values(
            workflow_id=workflow_id,
            max_parallel=max_parallel,
            dependency_jobs=dependencies,
        )

        job_id = str(uuid.uuid4())
        self._validate_no_dependency_cycle(job_id, dependencies)
        now = utc_now_iso()

        job = {
            "id": job_id,
            "job_type": "docs_refresh",
            "status": "blocked" if dependencies else "queued",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "app_doc_id": app_doc_id,
            "app_id": app_doc.get("app_id"),
            "dop_app_name": content.get("name"),
            "dop_app_icon": content.get("icon"),
            "doc_type": doc_type,
            "doc_type_title": doc_type_entry.get("title", doc_type),
            "doc_name": content.get("name"),
            "summary": None,
            "failure": None,
            "logs": [],
            "result": None,
            "depends_on_job_ids": [str(dep["id"]) for dep in dependencies],
            "dependent_job_ids": [],
            "workflow_id": resolved_workflow_id,
            "workflow_max_parallel": resolved_max_parallel,
            "blocking_reason": "Waiting for dependencies" if dependencies else None,
            "source": source,
            "can_cancel": False,
            "cancel_requested": False,
        }

        self._normalize_job_dependency_fields(job)

        with self._lock:
            self._jobs[job_id] = job
            self._persist_job(job)

        self._link_dependencies(job, dependencies)

        self._prune_old_jobs()
        self._schedule_ready_jobs()

        return self._serialize_job(job, include_logs=True)

    async def create_doc_action_job(
        self,
        doc_id: int,
        action_name: str,
        depends_on_job_ids: list[str] | None = None,
        workflow_id: str | None = None,
        max_parallel: int | None = None,
    ) -> dict[str, Any]:
        raw_doc = self.database.get_doc(doc_id)
        doc = raw_doc.to_dict() if hasattr(raw_doc, "to_dict") else raw_doc
        doc_type = doc.get("doc_type")
        if not doc_type:
            raise ValueError("Document doc_type is not configured")

        doc_type_str = str(doc_type)
        app_doc: dict[str, Any] | None = None
        app_config: dict[str, Any] | None = None

        app_id = doc.get("app_id")
        if app_id:
            app_docs = self.database.list_docs(doc_type="dop_app", app_id=app_id)
            if len(app_docs) > 0:
                raw_app_doc = app_docs[0]
                app_doc = raw_app_doc.to_dict() if hasattr(raw_app_doc, "to_dict") else raw_app_doc
                content = app_doc.get("content", {})
                plugin_key = content.get("plugin_key")
                if not isinstance(plugin_key, str) or not plugin_key.strip():
                    raise ValueError("Application plugin_key is not configured")

                registry = self.get_registry()
                app_config = registry.get_app_config(plugin_key)
                if app_config is None:
                    raise ValueError(f"Unknown plugin app: {plugin_key}")

        if app_config is None:
            registry = self.get_registry()
            for candidate in registry.list_app_configs():
                candidate_doc_type_entry = self._find_doc_type_entry(candidate, doc_type_str)
                if candidate_doc_type_entry is None:
                    continue
                candidate_action = self._find_action_entry(candidate_doc_type_entry, action_name)
                if candidate_action is None:
                    continue
                app_config = candidate
                break

        if app_config is None:
            raise ValueError(f"Action {action_name} not found for doc type {doc_type_str}")

        if app_doc is not None:
            app_content = dict(app_doc.get("content", {}))
            app_content["doc_types"] = app_config.get("doc_types", app_content.get("doc_types", []))
            app_doc = {**app_doc, "content": app_content}

        doc_type_entry = self._find_doc_type_entry(app_config, doc_type_str)
        if doc_type_entry is None:
            plugin_key = str(app_config.get("plugin_key", ""))
            raise ValueError(f"Doc type {doc_type_str} not found for plugin {plugin_key}")

        action_entry = self._find_action_entry(doc_type_entry, action_name)
        if action_entry is None:
            raise ValueError(f"Action {action_name} not found for doc type {doc_type}")

        source = action_entry.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"Action {action_name} does not define source")

        source_path = (self.plugins_dir / source).resolve()
        if not source_path.exists():
            raise ValueError(f"Plugin source not found: {source}")

        dependencies = self._collect_dependency_jobs(depends_on_job_ids or [])
        resolved_workflow_id, resolved_max_parallel = self._resolve_workflow_values(
            workflow_id=workflow_id,
            max_parallel=max_parallel,
            dependency_jobs=dependencies,
        )

        job_id = str(uuid.uuid4())
        self._validate_no_dependency_cycle(job_id, dependencies)
        now = utc_now_iso()
        action_title = action_entry.get("title") or action_name
        doc_name = (doc.get("content", {}) or {}).get("name")
        if not isinstance(doc_name, str) or not doc_name.strip():
            doc_name = None

        job = {
            "id": job_id,
            "job_type": "doc_action",
            "status": "blocked" if dependencies else "queued",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "app_doc_id": int(app_doc.get("id")) if app_doc else 0,
            "app_id": app_doc.get("app_id") if app_doc else None,
            "dop_app_name": (app_doc.get("content", {}) if app_doc else {}).get("name") or app_config.get("name"),
            "dop_app_icon": (app_doc.get("content", {}) if app_doc else {}).get("icon") or app_config.get("icon"),
            "doc_type": doc_type,
            "doc_type_title": str(action_title),
            "doc_name": doc_name,
            "summary": None,
            "failure": None,
            "logs": [],
            "result": None,
            "depends_on_job_ids": [str(dep["id"]) for dep in dependencies],
            "dependent_job_ids": [],
            "workflow_id": resolved_workflow_id,
            "workflow_max_parallel": resolved_max_parallel,
            "blocking_reason": "Waiting for dependencies" if dependencies else None,
            "doc_id": doc_id,
            "action_name": action_name,
            "source": source,
            "can_cancel": False,
            "cancel_requested": False,
        }

        self._normalize_job_dependency_fields(job)

        with self._lock:
            self._jobs[job_id] = job
            self._persist_job(job)

        self._link_dependencies(job, dependencies)

        self._prune_old_jobs()
        self._schedule_ready_jobs()

        return self._serialize_job(job, include_logs=True)

    async def create_chat_message_job(
        self,
        thread_id: int,
        thread_name: str,
        copilot_session_id: str,
        prompt_text: str,
        merged_agents: list[dict[str, Any]],
        merged_doc_mentions: list[dict[str, Any]],
        unresolved_doc_queries: list[str],
        system_prompt: str,
        depends_on_job_ids: list[str] | None = None,
        workflow_id: str | None = None,
        max_parallel: int | None = None,
    ) -> dict[str, Any]:
        dependencies = self._collect_dependency_jobs(depends_on_job_ids or [])
        resolved_workflow_id, resolved_max_parallel = self._resolve_workflow_values(
            workflow_id=workflow_id,
            max_parallel=max_parallel,
            dependency_jobs=dependencies,
        )

        job_id = str(uuid.uuid4())
        self._validate_no_dependency_cycle(job_id, dependencies)
        now = utc_now_iso()

        job = {
            "id": job_id,
            "job_type": "chat_message",
            "status": "blocked" if dependencies else "queued",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "app_doc_id": 0,
            "app_id": None,
            "dop_app_name": "Chat",
            "dop_app_icon": None,
            "doc_type": "chat_message",
            "doc_type_title": "Chat Message",
            "doc_name": thread_name,
            "summary": None,
            "failure": None,
            "logs": [],
            "agent_events": [],
            "result": None,
            "depends_on_job_ids": [str(dep["id"]) for dep in dependencies],
            "dependent_job_ids": [],
            "workflow_id": resolved_workflow_id,
            "workflow_max_parallel": resolved_max_parallel,
            "blocking_reason": "Waiting for dependencies" if dependencies else None,
            "can_cancel": True,
            "cancel_requested": False,
            "thread_id": thread_id,
            "copilot_session_id": copilot_session_id,
            "prompt_text": prompt_text,
            "merged_agents": merged_agents,
            "merged_doc_mentions": merged_doc_mentions,
            "unresolved_doc_queries": unresolved_doc_queries,
            "system_prompt": system_prompt,
        }

        self._normalize_job_dependency_fields(job)

        with self._lock:
            self._jobs[job_id] = job
            self._persist_job(job)

        self._link_dependencies(job, dependencies)

        self._prune_old_jobs()
        self._schedule_ready_jobs()

        return self._serialize_job(job, include_logs=False)

    async def _run_job(self, job_id: str) -> None:
        job = self._load_job_record(job_id)
        if job is None:
            return
        job_type = str(job.get("job_type") or "")
        if job_type == "docs_refresh":
            await self._run_docs_refresh_job(job_id)
            return
        if job_type == "doc_action":
            await self._run_doc_action_job(job_id)
            return
        if job_type == "chat_message":
            await self._run_chat_message_job(job_id)
            return

    async def _run_chat_message_job(self, job_id: str) -> None:
        import asyncio

        with self._lock:
            job = self._jobs[job_id]
            if job.get("status") == "cancelled":
                return
            thread_id = job["thread_id"]
            copilot_session_id = job["copilot_session_id"]
            prompt_text = job["prompt_text"]
            merged_agents = job["merged_agents"]
            merged_doc_mentions = job["merged_doc_mentions"]
            unresolved_doc_queries = job["unresolved_doc_queries"]
            system_prompt = job["system_prompt"]

        result = await asyncio.to_thread(
            self._execute_chat_message_job_sync,
            job_id,
            thread_id,
            copilot_session_id,
            prompt_text,
            merged_agents,
            merged_doc_mentions,
            unresolved_doc_queries,
            system_prompt,
        )

        with self._lock:
            job = self._jobs[job_id]
            self._job_processes.pop(job_id, None)
            if result.get("cancelled"):
                job["status"] = "cancelled"
                job["summary"] = result["summary"]
                job["finished_at"] = utc_now_iso()
                job["blocking_reason"] = result["summary"]
                self._persist_job(job)
                downstream_message = f"Blocked by cancelled dependency {job_id}"
            else:
                job["finished_at"] = utc_now_iso()
                if result["ok"]:
                    job["status"] = "success"
                    job["summary"] = result["summary"]
                    if str(job.get("doc_name") or "").strip().lower() == "onboarding":
                        onboarding_payload = self._read_onboarding_result_file()
                        if onboarding_payload is not None:
                            job["result"] = {
                                "onboarding_products": onboarding_payload,
                                "source": "/tmp/onboarding.json",
                            }
                    downstream_message = None
                else:
                    job["status"] = "failed"
                    job["failure"] = result["failure"]
                    job["summary"] = result["summary"]
                    job["blocking_reason"] = result["summary"]
                    downstream_message = f"Blocked by failed dependency {job_id}"
                self._persist_job(job)

        if downstream_message is not None:
            self._fail_pending_dependents(job_id, downstream_message)
        self._schedule_ready_jobs()

    def _execute_chat_message_job_sync(
        self,
        job_id: str,
        thread_id: int,
        copilot_session_id: str,
        prompt_text: str,
        merged_agents: list[dict[str, Any]],
        merged_doc_mentions: list[dict[str, Any]],
        unresolved_doc_queries: list[str],
        system_prompt: str,
    ) -> dict[str, Any]:
        import asyncio
        from agents import AgentRunner

        stdout_writer = JobLogWriter(lambda message: self._append_log(job_id, "stdout", message))
        stderr_writer = JobLogWriter(lambda message: self._append_log(job_id, "stderr", message))

        log_file_path: Path | None = None
        if self.logs_dir is not None:
            log_file_path = self.logs_dir / f"{job_id}.jsonl"

        def _on_agent_event(text: str) -> None:
            self._append_agent_event(job_id, text)

        def _on_process_started(process: subprocess.Popen[str]) -> None:
            with self._lock:
                self._job_processes[job_id] = process

        try:
            with contextlib.redirect_stdout(stdout_writer), contextlib.redirect_stderr(stderr_writer):
                runner = AgentRunner()
                runner.sync_app_yaml_agent_profiles()
                AgentRunner.sync_custom_agent_profiles(self.database.list_chat_agents())
                result = asyncio.run(
                    runner.run_agent(
                        session_id=copilot_session_id,
                        system_prompt=system_prompt,
                        user_message=prompt_text,
                        cwd="",
                        mcp_servers=None,
                        event_callback=_on_agent_event,
                        log_file_path=log_file_path,
                        process_callback=_on_process_started,
                    )
                )

            stdout_writer.flush()
            stderr_writer.flush()

            assistant_text = str(result.get("output") or "").strip() or "Copilot returned an empty response."
            if self._is_cancel_requested(job_id):
                return {"ok": False, "cancelled": True, "summary": "Cancelled by user", "failure": None}
            self.database.add_chat_message(
                thread_id,
                role="assistant",
                content={
                    "text": assistant_text,
                    "agent_ids": [agent["id"] for agent in merged_agents],
                    "doc_mentions": merged_doc_mentions,
                    "unresolved_doc_queries": unresolved_doc_queries,
                    "copilot_session_id": copilot_session_id,
                },
            )
            return {"ok": True, "summary": "Message processed"}

        except Exception as error:  # noqa: BLE001
            if self._is_cancel_requested(job_id):
                return {
                    "ok": False,
                    "cancelled": True,
                    "summary": "Cancelled by user",
                    "failure": None,
                }
            stack = traceback.format_exc()
            self._append_log(job_id, "stderr", stack.strip())
            error_text = f"Copilot execution failed: {error}"
            try:
                self.database.add_chat_message(
                    thread_id,
                    role="assistant",
                    content={
                        "text": error_text,
                        "agent_ids": [agent["id"] for agent in merged_agents],
                        "doc_mentions": merged_doc_mentions,
                        "unresolved_doc_queries": unresolved_doc_queries,
                        "copilot_session_id": copilot_session_id,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                "ok": False,
                "summary": f"{type(error).__name__}: {error}",
                "failure": stack,
            }

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.get("cancel_requested"))

    @staticmethod
    def _read_onboarding_result_file() -> list[dict[str, Any]] | None:
        onboarding_path = Path("/tmp/onboarding.json")
        if not onboarding_path.exists():
            return None
        try:
            payload = json.loads(onboarding_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, list):
            return None

        normalized: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            product_id = str(item.get("id") or "").strip()
            resources_raw = item.get("related_resources")
            resources: list[dict[str, str]] = []
            if isinstance(resources_raw, list):
                for res in resources_raw:
                    if not isinstance(res, dict):
                        continue
                    git_repo = str(res.get("git_repo") or "").strip()
                    if git_repo:
                        resources.append({"git_repo": git_repo})
            if name and product_id:
                normalized.append({
                    "name": name,
                    "id": product_id,
                    "related_resources": resources,
                })
        return normalized

    async def _run_docs_refresh_job(self, job_id: str) -> None:
        import asyncio

        with self._lock:
            job = self._jobs[job_id]
            app_doc_id = int(job.get("app_doc_id") or 0)
            doc_type = str(job.get("doc_type") or "")
            source = str(job.get("source") or "")

        app_doc = self.database.get_doc(app_doc_id)
        source_path = (self.plugins_dir / source).resolve()
        if not source_path.exists():
            result = {
                "ok": False,
                "summary": f"Plugin source not found: {source}",
                "failure": f"Plugin source not found: {source}",
            }
        else:
            result = await asyncio.to_thread(self._execute_docs_job_sync, job_id, app_doc, doc_type, source_path)

        with self._lock:
            job = self._jobs[job_id]
            job["finished_at"] = utc_now_iso()
            if result["ok"]:
                job["status"] = "success"
                job["summary"] = result["summary"]
                job["result"] = {"count": result["count"]}
                downstream_message = None
            else:
                job["status"] = "failed"
                job["failure"] = result["failure"]
                job["summary"] = result["summary"]
                job["blocking_reason"] = result["summary"]
                downstream_message = f"Blocked by failed dependency {job_id}"
            self._persist_job(job)

        if downstream_message is not None:
            self._fail_pending_dependents(job_id, downstream_message)
        self._schedule_ready_jobs()

    async def _run_doc_action_job(self, job_id: str) -> None:
        import asyncio

        with self._lock:
            job = self._jobs[job_id]
            app_doc_id = int(job.get("app_doc_id") or 0)
            doc_id = int(job.get("doc_id") or 0)
            action_name = str(job.get("action_name") or "")
            source = str(job.get("source") or "")

        app_doc: dict[str, Any] | None = None
        if app_doc_id > 0:
            raw_app_doc = self.database.get_doc(app_doc_id)
            app_doc = raw_app_doc.to_dict() if hasattr(raw_app_doc, "to_dict") else raw_app_doc

        raw_doc = self.database.get_doc(doc_id)
        doc = raw_doc.to_dict() if hasattr(raw_doc, "to_dict") else raw_doc
        source_path = (self.plugins_dir / source).resolve()
        if not source_path.exists():
            result = {
                "ok": False,
                "summary": f"Plugin source not found: {source}",
                "failure": f"Plugin source not found: {source}",
            }
        else:
            result = await asyncio.to_thread(
                self._execute_doc_action_job_sync,
                job_id,
                app_doc,
                doc,
                action_name,
                source_path,
            )

        with self._lock:
            job = self._jobs[job_id]
            job["finished_at"] = utc_now_iso()
            if result["ok"]:
                job["status"] = "success"
                job["summary"] = result["summary"]
                job["result"] = result.get("result")
                downstream_message = None
            else:
                job["status"] = "failed"
                job["failure"] = result["failure"]
                job["summary"] = result["summary"]
                job["blocking_reason"] = result["summary"]
                downstream_message = f"Blocked by failed dependency {job_id}"
            self._persist_job(job)

        if downstream_message is not None:
            self._fail_pending_dependents(job_id, downstream_message)
        self._schedule_ready_jobs()

    def _execute_docs_job_sync(
        self,
        job_id: str,
        app_doc: dict[str, Any],
        doc_type: str,
        source_path: Path,
    ) -> dict[str, Any]:
        stdout_writer = JobLogWriter(lambda message: self._append_log(job_id, "stdout", message))
        stderr_writer = JobLogWriter(lambda message: self._append_log(job_id, "stderr", message))

        runtime_doc = RuntimeApplicationDoc(
            id=int(app_doc["id"]),
            app_id=app_doc.get("app_id"),
            doc_type=str(app_doc.get("doc_type")),
            settings=dict(app_doc.get("content", {}).get("settings", {})),
            content=dict(app_doc.get("content", {})),
        )

        try:
            with contextlib.redirect_stdout(stdout_writer), contextlib.redirect_stderr(stderr_writer):
                module = self._load_module(source_path)
                get_docs = getattr(module, "get_docs", None)
                if not callable(get_docs):
                    return {
                        "ok": False,
                        "summary": "Plugin does not expose get_docs(application_doc)",
                        "failure": "Plugin does not expose get_docs(application_doc)",
                    }

                result = get_docs(runtime_doc)

            stdout_writer.flush()
            stderr_writer.flush()

            if isinstance(result, dop.DopError):
                return {
                    "ok": False,
                    "summary": str(result),
                    "failure": str(result),
                }

            if not isinstance(result, list):
                return {
                    "ok": False,
                    "summary": "Plugin result is invalid",
                    "failure": f"Expected list[dict], got {type(result).__name__}",
                }

            normalized_docs: list[dict[str, Any]] = []
            for index, item in enumerate(result):
                if not isinstance(item, dict):
                    return {
                        "ok": False,
                        "summary": "Plugin result is invalid",
                        "failure": f"Expected list[dict], item {index} is {type(item).__name__}",
                    }
                normalized_docs.append(item)

            self.database.replace_docs_for_app_and_type(app_doc.get("app_id"), doc_type, normalized_docs)

            return {
                "ok": True,
                "summary": f"Loaded {len(normalized_docs)} docs",
                "count": len(normalized_docs),
            }
        except Exception as error:  # noqa: BLE001
            stack = traceback.format_exc()
            self._append_log(job_id, "stderr", stack.strip())
            return {
                "ok": False,
                "summary": f"{type(error).__name__}: {error}",
                "failure": stack,
            }

    def _execute_doc_action_job_sync(
        self,
        job_id: str,
        app_doc: dict[str, Any] | None,
        doc: dict[str, Any],
        action_name: str,
        source_path: Path,
    ) -> dict[str, Any]:
        stdout_writer = JobLogWriter(lambda message: self._append_log(job_id, "stdout", message))
        stderr_writer = JobLogWriter(lambda message: self._append_log(job_id, "stderr", message))

        runtime_app_doc = None
        if app_doc is not None:
            runtime_app_doc = RuntimeApplicationDoc(
                id=int(app_doc["id"]),
                app_id=app_doc.get("app_id"),
                doc_type=str(app_doc.get("doc_type")),
                settings=dict(app_doc.get("content", {}).get("settings", {})),
                content=dict(app_doc.get("content", {})),
            )

        runtime_doc = RuntimeApplicationDoc(
            id=int(doc["id"]),
            app_id=doc.get("app_id"),
            doc_type=str(doc.get("doc_type")),
            settings={},
            content=dict(doc.get("content", {})),
        )

        try:
            # Set up environment for git and ssh askpass.
            askpass_script = Path(__file__).parent / "askpass.py"
            command_env: dict[str, str] = {}
            if askpass_script.exists():
                command_env["GIT_ASKPASS"] = str(askpass_script)
                command_env["GIT_ASKPASS_PROMPT"] = "echo"
                command_env["SSH_ASKPASS"] = str(askpass_script)
                command_env["SSH_ASKPASS_REQUIRE"] = "force"
                command_env.setdefault("DISPLAY", ":0")
                command_env["DOP_ASKPASS_JOB_ID"] = job_id
                # Keep API URL configurable
                api_url = os.environ.get("DOP_API_URL", "http://localhost:10818")
                command_env["DOP_ASKPASS_API_URL"] = api_url

                ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK", "").strip()
                if ssh_auth_sock:
                    command_env["SSH_AUTH_SOCK"] = ssh_auth_sock

            _thread_runtime.command_env = command_env

            with contextlib.redirect_stdout(stdout_writer), contextlib.redirect_stderr(stderr_writer):
                module = self._load_module(source_path)
                do_action = getattr(module, "do_action", None)
                if not callable(do_action):
                    return {
                        "ok": False,
                        "summary": "Plugin does not expose do_action(dop_app, doc, action_name)",
                        "failure": "Plugin does not expose do_action(dop_app, doc, action_name)",
                    }

                result = do_action(runtime_app_doc, runtime_doc, action_name)
            _thread_runtime.command_env = None

            stdout_writer.flush()
            stderr_writer.flush()

            if isinstance(result, dop.DopError):
                return {
                    "ok": False,
                    "summary": str(result),
                    "failure": str(result),
                }

            if isinstance(result, str):
                return {
                    "ok": False,
                    "summary": result,
                    "failure": result,
                }

            return {
                "ok": True,
                "summary": "Action completed",
                "result": result,
            }
        except Exception as error:  # noqa: BLE001
            _thread_runtime.command_env = None
            stack = traceback.format_exc()
            self._append_log(job_id, "stderr", stack.strip())
            return {
                "ok": False,
                "summary": f"{type(error).__name__}: {error}",
                "failure": stack,
            }

    @staticmethod
    def _load_module(source_path: Path) -> Any:
        module_name = f"dop_plugin_{source_path.stem}_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, source_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Failed to load module from {source_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _append_log(self, job_id: str, stream: str, message: str) -> None:
        timestamp = utc_now_iso()
        try:
            self.database.append_job_log(job_id, stream, timestamp, message)
        except Exception:
            pass
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["logs"].append(
                {
                    "stream": stream,
                    "timestamp": timestamp,
                    "entry": message,
                }
            )

    def _append_agent_event(self, job_id: str, text: str) -> None:
        timestamp = utc_now_iso()
        try:
            self.database.append_job_agent_event(job_id, "activity", text, timestamp)
        except Exception:
            pass
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.setdefault("agent_events", []).append(
                {
                    "type": "activity",
                    "text": text,
                    "timestamp": timestamp,
                }
            )

    def get_agent_events(self, job_id: str, since_index: int = 0) -> list[dict[str, Any]]:
        """Return agent activity events starting from since_index."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return []
            events: list[dict[str, Any]] = list(job.get("agent_events") or [])

        if not events:
            events = self.database.list_job_agent_events(job_id)
            with self._lock:
                current = self._jobs.get(job_id)
                if current is not None:
                    current["agent_events"] = events
        return events[since_index:]

    @staticmethod
    def _find_doc_type_entry(app_config: dict[str, Any], doc_type: str) -> dict[str, Any] | None:
        for item in app_config.get("doc_types", []):
            if item.get("key") == doc_type:
                return item
        return None

    @staticmethod
    def _find_action_entry(doc_type_entry: dict[str, Any], action_name: str) -> dict[str, Any] | None:
        actions = doc_type_entry.get("actions")
        if not isinstance(actions, dict):
            return None
        action = actions.get(action_name)
        if not isinstance(action, dict):
            return None
        return action

    @staticmethod
    def _serialize_job(job: dict[str, Any], include_logs: bool) -> dict[str, Any]:
        payload = {
            "id": job["id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "app_doc_id": job["app_doc_id"],
            "app_id": job["app_id"],
            "dop_app_name": job["dop_app_name"],
            "dop_app_icon": job["dop_app_icon"],
            "doc_type": job["doc_type"],
            "doc_type_title": job["doc_type_title"],
            "doc_name": job["doc_name"],
            "summary": job["summary"],
            "failure": job["failure"],
            "result": job["result"],
            "can_cancel": bool(job.get("can_cancel")) and job["status"] in ("queued", "blocked", "running"),
            "depends_on_job_ids": list(job.get("depends_on_job_ids") or []),
            "dependent_job_ids": list(job.get("dependent_job_ids") or []),
            "workflow_id": job.get("workflow_id"),
            "workflow_max_parallel": int(job.get("workflow_max_parallel") or 1),
            "blocking_reason": job.get("blocking_reason"),
        }

        if job["job_type"] == "doc_action" and "action_name" in job:
            payload["action_name"] = job["action_name"]

        if job["job_type"] == "chat_message" and "thread_id" in job:
            payload["thread_id"] = job["thread_id"]

        if include_logs:
            payload["logs"] = list(job["logs"])
            payload["agent_events"] = list(job.get("agent_events") or [])

        return payload
