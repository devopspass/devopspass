from typing import Any

from db import Database, DopDoc


def doc_name_from_content(content: dict[str, Any]) -> str:
    for key in ("name", "title", "path"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def doc_to_ref(doc: DopDoc) -> dict[str, Any]:
    name = doc_name_from_content(doc.content)
    url_value = doc.content.get("url")
    url = str(url_value).strip() if isinstance(url_value, str) and url_value.strip() else None
    return {
        "app_id": doc.app_id,
        "doc_type": doc.doc_type,
        "name": name,
        **({"url": url} if url else {}),
    }


def normalize_doc_refs(
    database: Database,
    resources: list[Any],
    legacy_doc_cache: dict[int, DopDoc] | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    docs_by_id = legacy_doc_cache if legacy_doc_cache is not None else {}

    for resource in resources:
        if isinstance(resource, dict):
            app_id = str(resource.get("app_id") or "").strip()
            doc_type = str(resource.get("doc_type") or "").strip()
            name = str(resource.get("name") or "").strip()
            url_value = resource.get("url")
            url = str(url_value).strip() if isinstance(url_value, str) and url_value.strip() else None
            if app_id and doc_type and name:
                normalized.append({"app_id": app_id, "doc_type": doc_type, "name": name, "url": url})
            continue

        if isinstance(resource, str) and resource.strip().isdigit():
            doc_id = int(resource.strip())
            try:
                doc = docs_by_id.get(doc_id)
                if doc is None:
                    doc = database.get_doc(doc_id)
                    docs_by_id[doc_id] = doc
            except ValueError:
                continue

            name = doc_name_from_content(doc.content)
            if not doc.app_id or not name:
                continue

            url_value = doc.content.get("url")
            url = str(url_value).strip() if isinstance(url_value, str) and url_value.strip() else None
            normalized.append({"app_id": doc.app_id, "doc_type": doc.doc_type, "name": name, "url": url})

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for resource in normalized:
        key = "|".join(
            [
                resource["app_id"].lower(),
                resource["doc_type"].lower(),
                resource["name"].lower(),
                (resource.get("url") or "").lower(),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(resource)

    return unique


def resolve_doc_refs(
    database: Database,
    resources: list[dict[str, Any]],
    docs_cache: dict[tuple[str, str], list[DopDoc]] | None = None,
    facts_cache: dict[tuple[str, str, str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    docs_by_app_and_type = docs_cache if docs_cache is not None else {}
    facts_by_identity = facts_cache if facts_cache is not None else {}

    for resource in resources:
        app_id = str(resource.get("app_id") or "").strip()
        doc_type = str(resource.get("doc_type") or "").strip()
        name = str(resource.get("name") or "").strip().lower()
        url = str(resource.get("url") or "").strip().lower()
        if not app_id or not doc_type or not name:
            continue

        cache_key = (app_id, doc_type)
        docs = docs_by_app_and_type.get(cache_key)
        if docs is None:
            docs = database.list_docs(doc_type=doc_type, app_id=app_id, include_facts=False)
            docs_by_app_and_type[cache_key] = docs
        matched: DopDoc | None = None
        for doc in docs:
            doc_name = doc_name_from_content(doc.content).lower()
            doc_url_value = doc.content.get("url")
            doc_url = str(doc_url_value).strip().lower() if isinstance(doc_url_value, str) else ""

            if url and doc_url and url == doc_url:
                matched = doc
                break

            if doc_name and doc_name == name:
                matched = doc
                if not url:
                    break

        if matched is not None:
            matched_name = doc_name_from_content(matched.content).strip()
            matched_url_value = matched.content.get("url")
            matched_url = str(matched_url_value).strip() if isinstance(matched_url_value, str) else ""
            fact_key = (app_id, doc_type, matched_name, matched_url)

            has_fact = facts_by_identity.get(fact_key)
            if has_fact is None:
                has_fact = database.fact_exists(
                    app_id=app_id,
                    doc_type=doc_type,
                    name=matched_name,
                    url=matched_url or None,
                ) if matched_name else False
                facts_by_identity[fact_key] = has_fact

            payload = matched.to_dict()
            payload["fact"] = "__exists__" if has_fact else None
            resolved.append(payload)

    return resolved


def search_docs(
    database: Database,
    *,
    query: str | None = None,
    doc_type: str | None = None,
    app_id: str | None = None,
) -> list[DopDoc]:
    docs = database.list_docs(query=query, doc_type=doc_type, app_id=app_id)

    if query:
        fact_related_docs = database.list_docs_by_fact_query(query=query, doc_type=doc_type, app_id=app_id)
        existing_ids = {doc.id for doc in docs}
        for doc in fact_related_docs:
            if doc.id in existing_ids:
                continue
            docs.append(doc)
            existing_ids.add(doc.id)

    return docs