import os
from pathlib import Path
from typing import Any

from db import Database

_DB_NAME = "dop.sqlite3"


def get_db_path(data_dir: Path | None = None) -> Path:
    """Return the path to the SQLite database, resolved from DOP_DATA_DIR env var."""
    if data_dir is None:
        data_dir = Path(os.environ.get("DOP_DATA_DIR", "/workspace/.data"))
    return data_dir / _DB_NAME


def get_database() -> Database:
    """Open and return a Database instance using the configured DB path."""
    db_path = get_db_path()
    if not db_path.exists():
        raise RuntimeError(f"Database not found at {db_path}")
    return Database(db_path=db_path)


def update_doc_fact(app_id: str | None, doc_type: str, content: dict[str, Any], text: str) -> None:
    """Write fact text for a document identified by its name/url in the database."""
    name = content.get("name") or content.get("title") or content.get("path")
    if not name:
        raise RuntimeError("Document missing name/title/path for facts identity")

    url = content.get("url")
    name_value = str(name).strip()
    url_value = str(url).strip() if url else None

    database = get_database()
    database.update_facts(app_id, doc_type, name_value, url_value, text)
