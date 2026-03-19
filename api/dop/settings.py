from typing import Any

from dop.db import get_database


def get_dop_app_settings(app_id: str) -> dict[str, Any]:
    """
    Read dop_app settings from docs DB for the given app_id.

    Args:
        app_id: dop_app app_id value in docs table

    Returns:
        Settings dictionary from dop_app.content.settings, or empty dict
    """
    try:
        database = get_database()
        docs = database.list_docs(doc_type="dop_app", app_id=app_id)
        if not docs:
            return {}

        settings = docs[0].content.get("settings", {})
        if isinstance(settings, dict):
            return settings
    except Exception:
        return {}

    return {}
