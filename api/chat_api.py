import re
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from db import Database
from doc_refs import doc_to_ref, normalize_doc_refs, resolve_doc_refs, search_docs
from jobs import JobsManager
from schemas import ChatAgentCreate, ChatAgentUpdate, ChatMessageCreate, ChatThreadCreate, ChatThreadUpdate

AGENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
AGENT_MENTION_PATTERN = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9._-]{0,62})")
DOC_QUERY_PATTERN = re.compile(r"#(.+?)(?=(?:\s+@[a-zA-Z0-9][a-zA-Z0-9._-]{0,62})|\s*#|$)")
DEFAULT_LIBRARIAN_NAME = "devops_librarian"
DEFAULT_LIBRARIAN_TITLE = "DevOps Librarian"
DEFAULT_LIBRARIAN_PROMPT = """
You are DevOps Librarian.
Your role is to gather factual DevOps information from DevOps Pass AI knowledge, then return concise evidence-based answers.

When needed, use DevOps Pass AI MCP tools:
- list_doc_types
- search_docs
- get_doc

Behavior:
- Start with search_docs using focused terms from the request.
- Retrieve full details via get_doc for the most relevant hits.
- Prefer facts and references from docs over assumptions.
- If evidence is insufficient, say what is missing.

Reference links format (required when citing docs, results of get_doc/search_docs MCP tools):
- Use markdown link text as doc label, usually starting with '#', for example:
    [link text](dopdoc://doc_type=dop_env&app_id=devops-pass-ai&name=stg)
- URL format must be:
    dopdoc://doc_type=<doc_type>&app_id=<app_id>&name=<urlencoded_name>&url=<urlencoded_url>
- If URL is unavailable, omit the url parameter.
- Do not use plain text for internal doc references when a structured dopdoc link can be provided.
""".strip()

CHAT_SYSTEM_PROMPT = """
You are the main DevOps chat assistant.
If a user request requires looking up infrastructure facts, repo metadata, environments, deployment configuration, or other stored DevOps documentation,
decide whether to delegate information gathering to the `devops_librarian` custom agent.

Give reply to user but propose to use `devops_investigator` to dig deeper if needed.

If user request is not clearly fit for specific agent, before any further actions, try to find out in scope of which Product (dop_product) and/or Environment (dop_env) the user is asking, as this will help to narrow down the search space for all subsequent information retrieval steps.
If found product/env doesnt clearly cover the user request, you can ask follow-up questions to clarify the context and find the right product/env.

!IMPORTANT!
If any of MCP tools failing with networking issues - stop immidiately.

When referencing internal DevOps Pass AI documents in your final reply, always use markdown links with dopdoc format:
- [#Doc Name](dopdoc://doc_type=<doc_type>&app_id=<app_id>&name=<urlencoded_name>&url=<urlencoded_url>)
- If URL is unknown, omit the url parameter.
- Keep link text human-readable and include the document name.
- Before adding such link, make sure document with doc_type/app_id/name actually exists by using MCP tool search_docs or get_doc.

When user mentions dopdoc:// link, pull it via search_docs MCP tool to pull it into context
""".strip()


def create_chat_router(database: Database, jobs_manager: JobsManager) -> APIRouter:
    router = APIRouter()

    @router.get("/api/chat/agents")
    def list_chat_agents() -> list[dict[str, Any]]:
        return database.list_chat_agents()

    @router.post("/api/chat/agents")
    def add_chat_agent(payload: ChatAgentCreate) -> dict[str, Any]:
        name = _normalize_agent_name(payload.name)
        title = (payload.title or "").strip() or name
        prompt = payload.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")

        try:
            return database.add_chat_agent(
                name=name,
                title=title,
                prompt=prompt,
                description=(payload.description or "").strip() or None,
                model=(payload.model or "").strip() or None,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/chat/agents/{agent_id}")
    def get_chat_agent(agent_id: int) -> dict[str, Any]:
        try:
            return database.get_chat_agent(agent_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.put("/api/chat/agents/{agent_id}")
    def update_chat_agent(agent_id: int, payload: ChatAgentUpdate) -> dict[str, Any]:
        name = _normalize_agent_name(payload.name) if payload.name is not None else None
        prompt = payload.prompt.strip() if payload.prompt is not None else None
        if prompt is not None and not prompt:
            raise HTTPException(status_code=400, detail="prompt cannot be empty")

        try:
            return database.update_chat_agent(
                agent_id,
                name=name,
                title=(payload.title or "").strip() or None if payload.title is not None else None,
                prompt=prompt,
                description=(payload.description or "").strip() or None if payload.description is not None else None,
                model=(payload.model or "").strip() or None if payload.model is not None else None,
            )
        except ValueError as error:
            status_code = 404 if "not found" in str(error).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(error)) from error

    @router.delete("/api/chat/agents/{agent_id}")
    def delete_chat_agent(agent_id: int) -> dict[str, bool]:
        try:
            return {"deleted": database.delete_chat_agent(agent_id)}
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/chat/threads")
    def list_chat_threads() -> list[dict[str, Any]]:
        return [_thread_to_payload(database, thread, include_messages=False) for thread in database.list_chat_threads()]

    @router.post("/api/chat/threads")
    def add_chat_thread(payload: ChatThreadCreate) -> dict[str, Any]:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")

        try:
            thread = database.add_chat_thread(
                name=name,
                attached_docs=normalize_doc_refs(database, [item.model_dump() for item in payload.attached_docs]),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        return _thread_to_payload(database, thread, include_messages=True)

    @router.get("/api/chat/threads/{thread_id}")
    def get_chat_thread(thread_id: int) -> dict[str, Any]:
        try:
            thread = database.get_chat_thread(thread_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return _thread_to_payload(database, thread, include_messages=True)

    @router.put("/api/chat/threads/{thread_id}")
    def update_chat_thread(thread_id: int, payload: ChatThreadUpdate) -> dict[str, Any]:
        try:
            thread = database.update_chat_thread(
                thread_id,
                name=payload.name.strip() if payload.name is not None else None,
                attached_docs=(
                    normalize_doc_refs(database, [item.model_dump() for item in payload.attached_docs])
                    if payload.attached_docs is not None
                    else None
                ),
            )
        except ValueError as error:
            status_code = 404 if "not found" in str(error).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(error)) from error

        return _thread_to_payload(database, thread, include_messages=True)

    @router.delete("/api/chat/threads/{thread_id}")
    def delete_chat_thread(thread_id: int) -> dict[str, bool]:
        try:
            return {"deleted": database.delete_chat_thread(thread_id)}
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/chat/threads/{thread_id}/messages")
    async def add_chat_message(thread_id: int, payload: ChatMessageCreate) -> dict[str, Any]:
        try:
            thread = database.get_chat_thread(thread_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")

        _ensure_default_librarian_agent(database)

        payload_agent_names = [
            item.strip().lower()
            for item in payload.agent_mentions
            if isinstance(item, str) and item.strip()
        ]
        payload_doc_mentions = normalize_doc_refs(database, [item.model_dump() for item in payload.doc_mentions])
        parsed = _parse_user_message(database, text, parse_docs=len(payload_doc_mentions) == 0)

        merged_agents = _merge_agents(database, parsed["agents"], payload_agent_names)
        merged_doc_mentions = _merge_doc_mentions(parsed["doc_mentions"], payload_doc_mentions)
        first_user_message = _is_first_user_message(database, thread_id)

        copilot_session_id = str(thread.get("copilot_session_id") or "").strip()
        if not copilot_session_id:
            copilot_session_id = str(uuid.uuid4())
            thread = database.set_chat_thread_copilot_session_id(thread_id, copilot_session_id)

        prompt_text = text
        if first_user_message:
            prompt_text = _build_first_message_prompt(database, thread, merged_doc_mentions, text)

        database.add_chat_message(
            thread_id,
            role="user",
            content={
                "text": text,
                "agent_ids": [agent["id"] for agent in merged_agents],
                "doc_mentions": merged_doc_mentions,
                "unresolved_doc_queries": parsed["unresolved_doc_queries"],
                "copilot_session_id": copilot_session_id,
            },
        )

        status_text = _build_status_message(merged_agents, merged_doc_mentions, parsed["unresolved_doc_queries"])
        if status_text:
            database.add_chat_message(
                thread_id,
                role="assistant",
                content={
                    "text": status_text,
                    "agent_ids": [agent["id"] for agent in merged_agents],
                    "doc_mentions": merged_doc_mentions,
                    "unresolved_doc_queries": parsed["unresolved_doc_queries"],
                    "copilot_session_id": copilot_session_id,
                },
            )

        thread_name = str(thread.get("name") or "")
        job = await jobs_manager.create_chat_message_job(
            thread_id=thread_id,
            thread_name=thread_name,
            copilot_session_id=copilot_session_id,
            prompt_text=prompt_text,
            merged_agents=merged_agents,
            merged_doc_mentions=merged_doc_mentions,
            unresolved_doc_queries=parsed["unresolved_doc_queries"],
            system_prompt=CHAT_SYSTEM_PROMPT,
        )

        return {
            "thread": _thread_to_payload(database, database.get_chat_thread(thread_id), include_messages=True),
            "job_id": job["id"],
        }

    return router


def _normalize_agent_name(value: str) -> str:
    name = value.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not AGENT_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="name must contain only lowercase letters, numbers, dots, dashes, or underscores")
    return name


def _thread_to_payload(database: Database, thread: dict[str, Any], include_messages: bool) -> dict[str, Any]:
    docs_cache: dict[tuple[str, str], list[Any]] = {}
    facts_cache: dict[tuple[str, str, str, str], bool] = {}
    attached_docs = normalize_doc_refs(database, thread.get("attached_docs") or [])
    payload = {
        **thread,
        "attached_docs": attached_docs,
        "attached_docs_docs": resolve_doc_refs(database, attached_docs, docs_cache=docs_cache, facts_cache=facts_cache),
    }

    if not include_messages:
        return payload

    agents_by_id = {agent["id"]: agent for agent in database.list_chat_agents()}
    messages = database.list_chat_messages(int(thread["id"]))
    payload["messages"] = [
        _message_to_payload(database, message, agents_by_id=agents_by_id, docs_cache=docs_cache, facts_cache=facts_cache)
        for message in messages
    ]
    return payload


def _message_to_payload(
    database: Database,
    message: dict[str, Any],
    *,
    agents_by_id: dict[int, dict[str, Any]],
    docs_cache: dict[tuple[str, str], list[Any]],
    facts_cache: dict[tuple[str, str, str, str], bool],
) -> dict[str, Any]:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    doc_mentions = normalize_doc_refs(database, content.get("doc_mentions") or [])
    agent_ids = content.get("agent_ids") if isinstance(content.get("agent_ids"), list) else []

    return {
        **message,
        "text": str(content.get("text") or ""),
        "agent_mentions": [agents_by_id[agent_id] for agent_id in agent_ids if isinstance(agent_id, int) and agent_id in agents_by_id],
        "doc_mentions": doc_mentions,
        "doc_mentions_docs": resolve_doc_refs(database, doc_mentions, docs_cache=docs_cache, facts_cache=facts_cache),
        "unresolved_doc_queries": [
            str(item).strip()
            for item in (content.get("unresolved_doc_queries") or [])
            if str(item).strip()
        ],
    }


def _parse_user_message(database: Database, text: str, *, parse_docs: bool = True) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    seen_agent_ids: set[int] = set()
    for match in AGENT_MENTION_PATTERN.finditer(text):
        agent = database.get_chat_agent_by_name(match.group(1).lower())
        if agent is None or int(agent["id"]) in seen_agent_ids:
            continue
        seen_agent_ids.add(int(agent["id"]))
        agents.append(agent)

    doc_mentions: list[dict[str, Any]] = []
    unresolved_doc_queries: list[str] = []
    seen_doc_keys: set[str] = set()

    if parse_docs:
        for match in DOC_QUERY_PATTERN.finditer(text):
            query = match.group(1).strip()
            if not query:
                continue

            docs = search_docs(database, query=query)
            if len(docs) == 0:
                unresolved_doc_queries.append(query)
                continue

            ref = doc_to_ref(docs[0])
            key = "|".join(
                [
                    str(ref.get("app_id") or "").lower(),
                    str(ref.get("doc_type") or "").lower(),
                    str(ref.get("name") or "").lower(),
                    str(ref.get("url") or "").lower(),
                ]
            )
            if key in seen_doc_keys:
                continue
            seen_doc_keys.add(key)
            doc_mentions.append(ref)

    return {
        "agents": agents,
        "doc_mentions": doc_mentions,
        "unresolved_doc_queries": unresolved_doc_queries,
    }


def _build_status_message(
    agents: list[dict[str, Any]],
    doc_mentions: list[dict[str, Any]],
    unresolved_doc_queries: list[str],
) -> str:
    parts: list[str] = []

    if agents:
        parts.append("Agents mentioned: " + ", ".join(f"@{agent['name']}" for agent in agents))
    if doc_mentions:
        parts.append("Docs added to context: " + ", ".join(f"#{doc['name']}" for doc in doc_mentions))
    if unresolved_doc_queries:
        parts.append("Docs not found: " + ", ".join(f"#{query}" for query in unresolved_doc_queries))

    if not parts:
        return ""

    return "; ".join(parts) + "."


def _merge_agents(database: Database, parsed_agents: list[dict[str, Any]], payload_names: list[str]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for agent in parsed_agents:
        agent_id = int(agent["id"])
        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)
        merged.append(agent)

    for name in payload_names:
        agent = database.get_chat_agent_by_name(name)
        if agent is None:
            continue
        agent_id = int(agent["id"])
        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)
        merged.append(agent)

    return merged


def _merge_doc_mentions(parsed_doc_mentions: list[dict[str, Any]], payload_doc_mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _key(ref: dict[str, Any]) -> str:
        return "|".join(
            [
                str(ref.get("app_id") or "").lower(),
                str(ref.get("doc_type") or "").lower(),
                str(ref.get("name") or "").lower(),
                str(ref.get("url") or "").lower(),
            ]
        )

    for ref in [*parsed_doc_mentions, *payload_doc_mentions]:
        key = _key(ref)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(ref)

    return merged


def _is_first_user_message(database: Database, thread_id: int) -> bool:
    messages = database.list_chat_messages(thread_id)
    return all(str(message.get("role") or "") != "user" for message in messages)


def _ensure_default_librarian_agent(database: Database) -> None:
    existing = database.get_chat_agent_by_name(DEFAULT_LIBRARIAN_NAME)
    if existing is not None:
        return

    database.add_chat_agent(
        name=DEFAULT_LIBRARIAN_NAME,
        title=DEFAULT_LIBRARIAN_TITLE,
        prompt=DEFAULT_LIBRARIAN_PROMPT,
        description="Finds DevOps infrastructure facts from stored docs via MCP tools.",
        model='gpt-5-mini',
    )


def _build_first_message_prompt(
    database: Database,
    thread: dict[str, Any],
    message_doc_mentions: list[dict[str, Any]],
    user_text: str,
) -> str:
    attached_refs = normalize_doc_refs(database, thread.get("attached_docs") or [])
    all_refs = _merge_doc_mentions(attached_refs, message_doc_mentions)

    context_lines: list[str] = []
    for ref in all_refs[:30]:
        app_id = str(ref.get("app_id") or "")
        doc_type = str(ref.get("doc_type") or "")
        name = str(ref.get("name") or "")
        url = str(ref.get("url") or "").strip()

        # Build dopdoc:// URL with URL-encoded parameters
        dopdoc_url = f"dopdoc://doc_type={quote(doc_type, safe='')}&app_id={quote(app_id, safe='')}&name={quote(name, safe='')}"
        if url:
            dopdoc_url += f"&url={quote(url, safe='')}"

        line = f"[#{name}]({dopdoc_url})"
        context_lines.append(line)

    if len(context_lines) == 0:
        return user_text

    context_text = "\n".join(context_lines)
    return (
        f"{context_text}\n\n"
        "User request:\n"
        f"{user_text}"
    )
