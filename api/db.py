import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DopDoc:
    app_id: str | None
    doc_type: str
    content: dict[str, Any]
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    fact: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return default

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "app_id": self.app_id,
            "doc_type": self.doc_type,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "fact": self.fact,
        }

class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT,
                    doc_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT,
                    doc_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT,
                    fact TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_docs_doc_type ON docs(doc_type)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_docs_app_id ON docs(app_id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_docs_app_type_updated ON docs(app_id, doc_type, updated_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_docs_updated_id ON docs(updated_at DESC, id DESC)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_docs_content ON docs(content)")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_identity ON facts(app_id, doc_type, name, url)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_facts_doc_type ON facts(doc_type)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_facts_app_id ON facts(app_id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_app_type ON facts(app_id, doc_type)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_app_type_updated ON facts(app_id, doc_type, updated_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_doc_type_updated ON facts(doc_type, updated_at DESC, id DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    model TEXT,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    attached_docs TEXT NOT NULL,
                    copilot_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chat_agents_name ON chat_agents(name)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_updated ON chat_threads(updated_at DESC, id DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_id ON chat_messages(thread_id, id ASC)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    app_doc_id INTEGER NOT NULL,
                    app_id TEXT,
                    dop_app_name TEXT,
                    dop_app_icon TEXT,
                    doc_type TEXT NOT NULL,
                    doc_type_title TEXT NOT NULL,
                    doc_name TEXT,
                    summary TEXT,
                    failure TEXT,
                    result TEXT,
                    can_cancel INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    entry TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id ON job_logs(job_id, id ASC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_job_agent_events_job_id_id ON job_agent_events(job_id, id ASC)")
            self._ensure_column(connection, "chat_threads", "copilot_session_id", "TEXT")
            self._ensure_column(connection, "chat_agents", "model", "TEXT")
            self._ensure_column(connection, "jobs", "can_cancel", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "jobs", "metadata", "TEXT NOT NULL DEFAULT '{}'")
            connection.commit()

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_type_sql: str) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column_name in existing:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}")

    def add_doc(self, doc: DopDoc) -> DopDoc:
        created_at = utc_now_iso()
        content_json = json.dumps(doc.content, ensure_ascii=False)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO docs (app_id, doc_type, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc.app_id, doc.doc_type, content_json, created_at, created_at),
            )
            connection.commit()
            doc_id = cursor.lastrowid
        return self.get_doc(doc_id)

    def get_doc(self, doc_id: int) -> DopDoc:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM docs WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            raise ValueError(f"Document {doc_id} not found")
        doc = self._row_to_doc(row)
        self._attach_fact(doc)
        return doc

    def update_doc(self, doc_id: int, doc: DopDoc) -> DopDoc:
        updated_at = utc_now_iso()
        content_json = json.dumps(doc.content, ensure_ascii=False)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE docs
                SET app_id = ?, content = ?, updated_at = ?
                WHERE id = ?
                """,
                (doc.app_id, content_json, updated_at, doc_id),
            )
            connection.commit()

        if cursor.rowcount == 0:
            raise ValueError(f"Document {doc_id} not found")

        return self.get_doc(doc_id)

    def delete_doc(self, doc_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
            connection.commit()
        return cursor.rowcount > 0

    def delete_docs_by_app_id(self, app_id: str | None) -> int:
        with self._connect() as connection:
            docs_cursor = connection.execute("DELETE FROM docs WHERE app_id IS ?", (app_id,))
            connection.execute("DELETE FROM facts WHERE app_id IS ?", (app_id,))
            connection.commit()
        return int(docs_cursor.rowcount)

    def list_docs(
        self,
        query: str | None = None,
        doc_type: str | None = None,
        app_id: str | None = None,
        include_facts: bool = True,
    ) -> list[DopDoc]:
        clauses: list[str] = []
        params: list[str] = []

        if doc_type:
            clauses.append("doc_type = ?")
            params.append(doc_type)

        if app_id:
            clauses.append("app_id = ?")
            params.append(app_id)

        if query:
            for term in self._query_terms(query):
                clauses.append("LOWER(content) LIKE ?")
                params.append(f"%{term}%")

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM docs {where_clause} ORDER BY updated_at DESC, id DESC"

        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        docs = [self._row_to_doc(row) for row in rows]
        if include_facts:
            for doc in docs:
                self._attach_fact(doc)
        return docs

    def list_distinct_doc_types(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT doc_type, app_id, COUNT(*) AS count
                FROM docs
                GROUP BY doc_type, app_id
                ORDER BY doc_type, app_id
                """
            ).fetchall()
        return [
            {"doc_type": row["doc_type"], "app_id": row["app_id"], "count": row["count"]}
            for row in rows
        ]

    def list_docs_by_fact_query(
        self,
        query: str,
        doc_type: str | None = None,
        app_id: str | None = None,
        include_facts: bool = True,
    ) -> list[DopDoc]:
        terms = self._query_terms(query)
        if len(terms) == 0:
            return []

        clauses: list[str] = []
        params: list[str] = []

        if doc_type:
            clauses.append("doc_type = ?")
            params.append(doc_type)

        if app_id:
            clauses.append("app_id = ?")
            params.append(app_id)

        for term in terms:
            clauses.append("LOWER(fact) LIKE ?")
            params.append(f"%{term}%")

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT app_id, doc_type, name, url "
            f"FROM facts {where_clause} ORDER BY updated_at DESC, id DESC"
        )

        with self._connect() as connection:
            matched_fact_rows = connection.execute(sql, params).fetchall()

        if len(matched_fact_rows) == 0:
            return []

        fact_identities = {
            (row["app_id"], row["doc_type"], row["name"], row["url"]) for row in matched_fact_rows
        }
        # Candidate scan is needed to map fact identity -> docs.
        # Keep this pass cheap by skipping fact attachment for every candidate doc.
        candidate_docs = self.list_docs(doc_type=doc_type, app_id=app_id, include_facts=False)

        related_docs: list[DopDoc] = []
        for doc in candidate_docs:
            name, url = self._fact_identity_from_content(doc.content)
            if not name:
                continue
            if (doc.app_id, doc.doc_type, name, url) in fact_identities:
                related_docs.append(doc)

        if include_facts:
            for doc in related_docs:
                self._attach_fact(doc)
        return related_docs

    def replace_docs_for_app_and_type(
        self,
        app_id: str | None,
        doc_type: str,
        docs: list[dict[str, Any]],
    ) -> list[DopDoc]:
        created_at = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM docs WHERE app_id IS ? AND doc_type = ?",
                (app_id, doc_type),
            )

            inserted_ids: list[int] = []
            for content in docs:
                content_json = json.dumps(content, ensure_ascii=False)
                cursor = connection.execute(
                    """
                    INSERT INTO docs (app_id, doc_type, content, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (app_id, doc_type, content_json, created_at, created_at),
                )
                inserted_ids.append(int(cursor.lastrowid))

            connection.commit()

        return [self.get_doc(doc_id) for doc_id in inserted_ids]

    def update_facts(
        self,
        app_id: str | None,
        doc_type: str,
        name: str,
        url: str | None,
        text: str,
    ) -> None:
        if not name:
            raise ValueError("facts.name is required")

        created_at = utc_now_iso()
        updated_at = created_at
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM facts WHERE app_id IS ? AND doc_type = ? AND name = ? AND url IS ?",
                (app_id, doc_type, name, url),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE facts SET fact = ?, updated_at = ? WHERE id = ?",
                    (text, updated_at, existing["id"]),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO facts (app_id, doc_type, name, url, fact, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                    ,
                    (app_id, doc_type, name, url, text, created_at, updated_at),
                )
            connection.commit()

    def fact_exists(self, app_id: str | None, doc_type: str, name: str, url: str | None) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM facts WHERE app_id IS ? AND doc_type = ? AND name = ? AND url IS ? LIMIT 1",
                (app_id, doc_type, name, url),
            ).fetchone()
        return row is not None

    def upsert_job(self, job: dict[str, Any]) -> None:
        metadata: dict[str, Any] = {}
        for key, value in job.items():
            if key in {
                "id",
                "job_type",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "app_doc_id",
                "app_id",
                "dop_app_name",
                "dop_app_icon",
                "doc_type",
                "doc_type_title",
                "doc_name",
                "summary",
                "failure",
                "result",
                "can_cancel",
                "cancel_requested",
                "logs",
                "agent_events",
            }:
                continue
            metadata[key] = value

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id,
                    job_type,
                    status,
                    created_at,
                    started_at,
                    finished_at,
                    app_doc_id,
                    app_id,
                    dop_app_name,
                    dop_app_icon,
                    doc_type,
                    doc_type_title,
                    doc_name,
                    summary,
                    failure,
                    result,
                    can_cancel,
                    cancel_requested,
                    metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    job_type=excluded.job_type,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    app_doc_id=excluded.app_doc_id,
                    app_id=excluded.app_id,
                    dop_app_name=excluded.dop_app_name,
                    dop_app_icon=excluded.dop_app_icon,
                    doc_type=excluded.doc_type,
                    doc_type_title=excluded.doc_type_title,
                    doc_name=excluded.doc_name,
                    summary=excluded.summary,
                    failure=excluded.failure,
                    result=excluded.result,
                    can_cancel=excluded.can_cancel,
                    cancel_requested=excluded.cancel_requested,
                    metadata=excluded.metadata
                """,
                (
                    str(job["id"]),
                    str(job["job_type"]),
                    str(job["status"]),
                    str(job["created_at"]),
                    job.get("started_at"),
                    job.get("finished_at"),
                    int(job.get("app_doc_id") or 0),
                    job.get("app_id"),
                    job.get("dop_app_name"),
                    job.get("dop_app_icon"),
                    str(job.get("doc_type") or ""),
                    str(job.get("doc_type_title") or ""),
                    job.get("doc_name"),
                    job.get("summary"),
                    job.get("failure"),
                    json.dumps(job.get("result"), ensure_ascii=False) if job.get("result") is not None else None,
                    1 if bool(job.get("can_cancel")) else 0,
                    1 if bool(job.get("cancel_requested")) else 0,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            connection.commit()

    def append_job_log(self, job_id: str, stream: str, timestamp: str, entry: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO job_logs (job_id, stream, timestamp, entry)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, stream, timestamp, entry),
            )
            connection.commit()

    def append_job_agent_event(self, job_id: str, event_type: str, text: str, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO job_agent_events (job_id, event_type, text, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, event_type, text, timestamp),
            )
            connection.commit()

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_job_logs(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT stream, timestamp, entry
                FROM job_logs
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "stream": str(row["stream"]),
                "timestamp": str(row["timestamp"]),
                "entry": str(row["entry"]),
            }
            for row in rows
        ]

    def list_job_agent_events(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, text, timestamp
                FROM job_agent_events
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "type": str(row["event_type"]),
                "text": str(row["text"]),
                "timestamp": str(row["timestamp"]),
            }
            for row in rows
        ]

    def mark_incomplete_jobs_failed(self, summary: str) -> int:
        finished_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET
                    status = 'failed',
                    summary = ?,
                    failure = ?,
                    finished_at = ?,
                    can_cancel = 0,
                    cancel_requested = 1
                WHERE status IN ('queued', 'blocked', 'running')
                """,
                (summary, summary, finished_at),
            )
            connection.commit()
        return int(cursor.rowcount)

    def delete_old_jobs(self, cutoff_created_at: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE created_at < ?",
                (cutoff_created_at,),
            )
            connection.commit()
        return int(cursor.rowcount)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        metadata_raw = row["metadata"] if "metadata" in row.keys() else "{}"
        try:
            metadata = json.loads(metadata_raw) if metadata_raw else {}
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        result_raw = row["result"] if "result" in row.keys() else None
        result: Any = None
        if result_raw is not None:
            try:
                result = json.loads(result_raw)
            except json.JSONDecodeError:
                result = None

        payload = {
            "id": row["id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "app_doc_id": int(row["app_doc_id"] or 0),
            "app_id": row["app_id"],
            "dop_app_name": row["dop_app_name"],
            "dop_app_icon": row["dop_app_icon"],
            "doc_type": row["doc_type"],
            "doc_type_title": row["doc_type_title"],
            "doc_name": row["doc_name"],
            "summary": row["summary"],
            "failure": row["failure"],
            "result": result,
            "can_cancel": bool(row["can_cancel"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "logs": [],
            "agent_events": [],
        }
        payload.update(metadata)
        return payload

    def list_chat_agents(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_agents ORDER BY LOWER(name) ASC, id ASC"
            ).fetchall()
        return [self._row_to_chat_agent(row) for row in rows]

    def get_chat_agent(self, agent_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM chat_agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise ValueError(f"Chat agent {agent_id} not found")
        return self._row_to_chat_agent(row)

    def get_chat_agent_by_name(self, name: str) -> dict[str, Any] | None:
        normalized = name.strip().lower()
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM chat_agents WHERE name = ?", (normalized,)).fetchone()
        if row is None:
            return None
        return self._row_to_chat_agent(row)

    def add_chat_agent(
        self,
        name: str,
        title: str,
        prompt: str,
        description: str | None,
        model: str | None = None,
    ) -> dict[str, Any]:
        normalized_name = self._normalize_chat_agent_name(name)
        if not title.strip():
            raise ValueError("Agent title is required")
        if not prompt.strip():
            raise ValueError("Agent prompt is required")
        normalized_model = model.strip() if isinstance(model, str) else None
        if normalized_model == "":
            normalized_model = None

        created_at = utc_now_iso()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO chat_agents (name, title, prompt, description, model, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (normalized_name, title.strip(), prompt, description, normalized_model, created_at, created_at),
                )
                connection.commit()
                agent_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Chat agent '{normalized_name}' already exists") from error

        return self.get_chat_agent(agent_id)

    def update_chat_agent(
        self,
        agent_id: int,
        *,
        name: str | None = None,
        title: str | None = None,
        prompt: str | None = None,
        description: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_chat_agent(agent_id)
        next_name = self._normalize_chat_agent_name(name) if name is not None else existing["name"]
        next_title = title.strip() if title is not None else str(existing["title"])
        next_prompt = prompt if prompt is not None else str(existing["prompt"])
        next_description = description if description is not None else existing.get("description")
        if model is not None:
            next_model = model.strip() or None
        else:
            next_model = existing.get("model")

        if not next_title:
            raise ValueError("Agent title is required")
        if not next_prompt.strip():
            raise ValueError("Agent prompt is required")

        updated_at = utc_now_iso()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE chat_agents
                    SET name = ?, title = ?, prompt = ?, description = ?, model = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_name, next_title, next_prompt, next_description, next_model, updated_at, agent_id),
                )
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Chat agent '{next_name}' already exists") from error

        if cursor.rowcount == 0:
            raise ValueError(f"Chat agent {agent_id} not found")
        return self.get_chat_agent(agent_id)

    def delete_chat_agent(self, agent_id: int) -> bool:
        self.get_chat_agent(agent_id)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM chat_agents WHERE id = ?", (agent_id,))
            connection.commit()
        return cursor.rowcount > 0

    def list_chat_threads(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    t.*,
                    (
                        SELECT COUNT(*)
                        FROM chat_messages m
                        WHERE m.thread_id = t.id
                    ) AS message_count
                FROM chat_threads t
                ORDER BY t.updated_at DESC, t.id DESC
                """
            ).fetchall()
        return [self._row_to_chat_thread(row) for row in rows]

    def get_chat_thread(self, thread_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    t.*,
                    (
                        SELECT COUNT(*)
                        FROM chat_messages m
                        WHERE m.thread_id = t.id
                    ) AS message_count
                FROM chat_threads t
                WHERE t.id = ?
                """,
                (thread_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Chat thread {thread_id} not found")
        return self._row_to_chat_thread(row)

    def add_chat_thread(self, name: str, attached_docs: list[dict[str, Any]]) -> dict[str, Any]:
        thread_name = name.strip()
        if not thread_name:
            raise ValueError("Chat thread name is required")

        created_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chat_threads (name, attached_docs, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_name, json.dumps(attached_docs, ensure_ascii=False), created_at, created_at),
            )
            connection.commit()
            thread_id = int(cursor.lastrowid)
        return self.get_chat_thread(thread_id)

    def update_chat_thread(
        self,
        thread_id: int,
        *,
        name: str | None = None,
        attached_docs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_chat_thread(thread_id)
        next_name = name.strip() if name is not None else str(existing["name"])
        next_attached_docs = attached_docs if attached_docs is not None else existing.get("attached_docs", [])

        if not next_name:
            raise ValueError("Chat thread name is required")

        updated_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE chat_threads
                SET name = ?, attached_docs = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_name, json.dumps(next_attached_docs, ensure_ascii=False), updated_at, thread_id),
            )
            connection.commit()

        if cursor.rowcount == 0:
            raise ValueError(f"Chat thread {thread_id} not found")
        return self.get_chat_thread(thread_id)

    def set_chat_thread_copilot_session_id(self, thread_id: int, copilot_session_id: str | None) -> dict[str, Any]:
        self.get_chat_thread(thread_id)
        updated_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE chat_threads
                SET copilot_session_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (copilot_session_id, updated_at, thread_id),
            )
            connection.commit()

        if cursor.rowcount == 0:
            raise ValueError(f"Chat thread {thread_id} not found")
        return self.get_chat_thread(thread_id)

    def delete_chat_thread(self, thread_id: int) -> bool:
        self.get_chat_thread(thread_id)
        with self._connect() as connection:
            connection.execute("DELETE FROM chat_messages WHERE thread_id = ?", (thread_id,))
            cursor = connection.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
            connection.commit()
        return cursor.rowcount > 0

    def list_chat_messages(self, thread_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id ASC",
                (thread_id,),
            ).fetchall()
        return [self._row_to_chat_message(row) for row in rows]

    def add_chat_message(self, thread_id: int, *, role: str, content: dict[str, Any]) -> dict[str, Any]:
        thread = self.get_chat_thread(thread_id)
        message_role = role.strip().lower()
        if message_role not in {"user", "assistant", "system"}:
            raise ValueError("Chat message role must be user, assistant, or system")

        created_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chat_messages (thread_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, message_role, json.dumps(content, ensure_ascii=False), created_at),
            )
            connection.execute(
                "UPDATE chat_threads SET updated_at = ? WHERE id = ?",
                (created_at, thread["id"]),
            )
            connection.commit()
            message_id = int(cursor.lastrowid)

        with self._connect() as connection:
            row = connection.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise ValueError(f"Chat message {message_id} not found")
        return self._row_to_chat_message(row)

    @staticmethod
    def _normalize_chat_agent_name(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Agent name is required")
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", normalized) is None:
            raise ValueError("Agent name must contain only lowercase letters, numbers, dots, dashes, or underscores")
        return normalized

    @staticmethod
    def _row_to_chat_agent(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "title": row["title"],
            "prompt": row["prompt"],
            "description": row["description"],
            "model": row["model"] if "model" in row.keys() else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_chat_thread(row: sqlite3.Row) -> dict[str, Any]:
        attached_docs = json.loads(row["attached_docs"]) if row["attached_docs"] else []
        if not isinstance(attached_docs, list):
            attached_docs = []
        payload = {
            "id": row["id"],
            "name": row["name"],
            "attached_docs": attached_docs,
            "copilot_session_id": row["copilot_session_id"] if "copilot_session_id" in row.keys() else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if "message_count" in row.keys():
            payload["message_count"] = int(row["message_count"] or 0)
        return payload

    @staticmethod
    def _row_to_chat_message(row: sqlite3.Row) -> dict[str, Any]:
        content = json.loads(row["content"]) if row["content"] else {}
        if not isinstance(content, dict):
            content = {}
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "role": row["role"],
            "content": content,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_doc(row: sqlite3.Row) -> DopDoc:
        return DopDoc(
            id=row["id"],
            app_id=row["app_id"],
            doc_type=row["doc_type"],
            content=json.loads(row["content"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _attach_fact(self, doc: DopDoc) -> None:
        if doc.fact is not None:
            return
        name, url = self._fact_identity_from_content(doc.content)
        if not name:
            if doc.doc_type == "dop_product":
                doc.fact = self._get_latest_fact_for_doc_type(doc.app_id, doc.doc_type)
            return
        doc.fact = self._get_fact(doc.app_id, doc.doc_type, name, url)

    @staticmethod
    def _fact_identity_from_content(content: dict[str, Any]) -> tuple[str | None, str | None]:
        name = content.get("name") or content.get("title") or content.get("path")
        url = content.get("url")
        name_value = str(name).strip() if name else None
        url_value = str(url).strip() if url else None
        return name_value, url_value

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return [word.strip().lower() for word in query.split() if word.strip()]

    def _get_fact(self, app_id: str | None, doc_type: str, name: str, url: str | None) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fact FROM facts WHERE app_id IS ? AND doc_type = ? AND name = ? AND url IS ?",
                (app_id, doc_type, name, url),
            ).fetchone()
        if row is None:
            if doc_type == "dop_product":
                return self._get_latest_fact_for_doc_type(app_id, doc_type)
            return None
        return row["fact"]

    def _get_latest_fact_for_doc_type(self, app_id: str | None, doc_type: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fact FROM facts WHERE app_id IS ? AND doc_type = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (app_id, doc_type),
            ).fetchone()
        if row is None:
            return None
        return row["fact"]
