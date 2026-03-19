import asyncio
import importlib.util
import json
import os
import traceback
from pathlib import Path
from typing import Any

import dop

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

from agents import AgentRunner
from chat_api import create_chat_router
from db import Database, DopDoc
from doc_refs import normalize_doc_refs, resolve_doc_refs, search_docs as search_docs_by_query
from firebase_auth import FirebaseAuthVerifier, is_auth_enabled
from jobs import JobsManager, RuntimeApplicationDoc
from mcp_server import create_mcp_server
from plugins import PluginRegistry
from schemas import ApplicationCreate, ApplicationTest, ApplicationUpdate, AskPassRequest, AskPassResponse, DocActionJobCreate, DocCreate, DocsRefreshJobCreate, ProductCreate, ProductUpdate

ROOT_DIR = Path(__file__).resolve().parents[1]
PLUGINS_DIR = Path(os.environ.get("DOP_PLUGINS_DIR", ROOT_DIR / "plugins"))
DATA_DIR = Path(os.environ.get("DOP_DATA_DIR", ROOT_DIR / ".data"))
LOGS_DIR = Path(os.environ.get("DOP_LOGS_DIR", ROOT_DIR / "logs"))
DB_PATH = DATA_DIR / "dop.sqlite3"
JOBS_RETENTION_DAYS = int(os.environ.get("DOP_JOBS_RETENTION_DAYS", "7"))
JOBS_LIST_LIMIT = int(os.environ.get("DOP_JOBS_LIST_LIMIT", "100"))
WORKFLOW_MAX_PARALLEL = int(os.environ.get("DOP_WORKFLOW_MAX_PARALLEL", "3"))
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "devops-pass-ai")

app = FastAPI(title="DevOps Pass AI API", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = PluginRegistry(PLUGINS_DIR)
database = Database(DB_PATH)
firebase_auth = FirebaseAuthVerifier(
    project_id=FIREBASE_PROJECT_ID,
)


def _is_unprotected_api_endpoint(path: str, method: str) -> bool:
    if method == "GET":
        return True
    if method == "POST" and path == "/api/askpass/request":
        return True
    if method == "GET" and path.startswith("/api/askpass/answer/"):
        return True
    return False


def _extract_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    prefix = "bearer "
    if not authorization_header.lower().startswith(prefix):
        return None
    token = authorization_header[len(prefix):].strip()
    return token or None


def _unauthorized_json_response(request: Request, detail: str) -> JSONResponse:
    response = JSONResponse(status_code=401, content={"detail": detail})
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.middleware("http")
async def require_firebase_auth(request: Request, call_next: Any) -> Any:
    if not is_auth_enabled():
        return await call_next(request)

    # CORS preflight requests must not require auth headers.
    if request.method.upper() == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)

    if _is_unprotected_api_endpoint(path, request.method.upper()):
        return await call_next(request)

    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        return _unauthorized_json_response(request, "Missing Authorization bearer token")

    try:
        claims = firebase_auth.verify_token(token)
    except Exception as error:  # noqa: BLE001
        return _unauthorized_json_response(request, f"Invalid Firebase token: {error}")

    request.state.auth_user = claims
    return await call_next(request)


def get_registry() -> PluginRegistry:
    return registry


jobs = JobsManager(
    database=database,
    get_registry=get_registry,
    plugins_dir=PLUGINS_DIR,
    data_dir=DATA_DIR,
    logs_dir=LOGS_DIR,
    jobs_retention_days=JOBS_RETENTION_DAYS,
    jobs_list_limit=JOBS_LIST_LIMIT,
    workflow_max_parallel_default=WORKFLOW_MAX_PARALLEL,
)
app.include_router(create_chat_router(database, jobs))
mcp_server = create_mcp_server(database)
mcp_app = mcp_server.streamable_http_app()
app.mount("/mcp", mcp_app)
_mcp_lifespan_cm: Any | None = None


@app.on_event("startup")
async def _startup_mcp_app() -> None:
    global _mcp_lifespan_cm
    _mcp_lifespan_cm = mcp_app.router.lifespan_context(mcp_app)
    await _mcp_lifespan_cm.__aenter__()


@app.on_event("shutdown")
async def _shutdown_mcp_app() -> None:
    global _mcp_lifespan_cm
    if _mcp_lifespan_cm is not None:
        await _mcp_lifespan_cm.__aexit__(None, None, None)
        _mcp_lifespan_cm = None


def _product_to_dict(
    doc: DopDoc,
    docs_cache: dict[tuple[str, str], list[DopDoc]] | None = None,
    legacy_doc_cache: dict[int, DopDoc] | None = None,
    facts_cache: dict[tuple[str, str, str, str], bool] | None = None,
) -> dict[str, Any]:
    payload = doc.to_dict()
    content = dict(payload.get("content") or {})
    resources = normalize_doc_refs(database, content.get("resources") or [], legacy_doc_cache=legacy_doc_cache)
    content["resources"] = resources
    content["resources_docs"] = resolve_doc_refs(database, resources, docs_cache=docs_cache, facts_cache=facts_cache)
    payload["content"] = content
    return payload


def ensure_builtin_app() -> None:
    """Ensure the built-in devops-pass-ai application exists."""
    builtin_app_id = "devops-pass-ai"
    existing_apps = database.list_docs(doc_type="dop_app", app_id=builtin_app_id)

    if len(existing_apps) > 0:
        return

    app_config = registry.get_app_config("dop")
    if app_config is None:
        return

    content = {
        "plugin_key": "dop",
        "name": app_config.get("name"),
        "description": app_config.get("description"),
        "description_long": app_config.get("description_long"),
        "icon": app_config.get("icon"),
        "settings": {},
        "doc_types": app_config.get("doc_types", []),
    }
    database.add_doc(DopDoc(app_id=builtin_app_id, doc_type="dop_app", content=content))


ensure_builtin_app()


def sync_app_yaml_agent_profiles() -> None:
    try:
        AgentRunner(data_dir=DATA_DIR, plugins_dir=PLUGINS_DIR).sync_app_yaml_agent_profiles()
    except Exception as error:  # noqa: BLE001
        print(f"Failed to sync app.yaml agent profiles: {error}", flush=True)


def _load_script_module(source_path: Path) -> Any:
    module_name = f"dop_check_{source_path.stem}_{os.urandom(6).hex()}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {source_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_application_test(plugin_key: str, app_id: str | None, settings: dict[str, Any]) -> dict[str, str]:
    app_config = registry.get_app_config(plugin_key)
    if app_config is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin app: {plugin_key}")

    check_script = app_config.get("check_script")
    if not isinstance(check_script, str) or not check_script.strip():
        raise HTTPException(status_code=400, detail=f"Plugin {plugin_key} does not define check_script")

    check_path = (PLUGINS_DIR / check_script).resolve()
    if not check_path.exists():
        raise HTTPException(status_code=400, detail=f"Check script not found: {check_script}")

    runtime_app = RuntimeApplicationDoc(
        id=0,
        app_id=app_id,
        doc_type="dop_app",
        settings=dict(settings),
        content={
            "plugin_key": plugin_key,
            "name": app_config.get("name"),
            "settings": dict(settings),
        },
    )

    try:
        module = _load_script_module(check_path)
        do_test = getattr(module, "do_test", None)
        if not callable(do_test):
            raise HTTPException(status_code=400, detail=f"{check_script} does not expose do_test(dop_app)")

        result = do_test(runtime_app)
        if isinstance(result, dop.DopError):
            return {"status": "failed", "message": str(result)}

        if isinstance(result, dict):
            status = str(result.get("status") or "success").strip().lower()
            message = str(result.get("message") or "Test passed").strip()
            return {
                "status": "success" if status == "success" else "failed",
                "message": message or ("Test passed" if status == "success" else "Test failed"),
            }

        if isinstance(result, bool):
            return {
                "status": "success" if result else "failed",
                "message": "Test passed" if result else "Test failed",
            }

        if isinstance(result, str):
            status = "failed" if result.strip().lower().startswith("error") else "success"
            return {
                "status": status,
                "message": result.strip() or ("Test passed" if status == "success" else "Test failed"),
            }

        return {"status": "success", "message": "Test passed"}
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        print(traceback.format_exc(), flush=True)
        return {
            "status": "failed",
            "message": f"{type(error).__name__}: {error}",
        }


@app.get("/status")
def status() -> dict[str, Any]:
    return {"status": "ok", "db_path": str(DB_PATH), "plugins_dir": str(PLUGINS_DIR)}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    claims = getattr(request.state, "auth_user", None) or {}
    return {
        "uid": claims.get("uid"),
        "email": claims.get("email"),
        "email_verified": bool(claims.get("email_verified", False)),
    }


@app.get("/api/plugin-apps")
def list_plugin_apps() -> list[dict[str, Any]]:
    return registry.list_app_configs()


@app.post("/api/configs/reload")
def reload_configs() -> dict[str, Any]:
    global registry
    registry = PluginRegistry(PLUGINS_DIR)
    apps = registry.list_app_configs()
    return {"reloaded": True, "apps_count": len(apps)}


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return jobs.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    try:
        return jobs.cancel_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_events(job_id: str, since: int = Query(default=0)) -> StreamingResponse:
    """Server-Sent Events endpoint streaming agent activity events for a job."""
    if jobs.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    async def generate():
        last_index = since
        while True:
            job = jobs.get_job(job_id)
            if job is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
                return

            new_events = jobs.get_agent_events(job_id, since_index=last_index)
            for event in new_events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                last_index += 1

            status = job.get("status")
            if status in ("success", "failed", "cancelled") and not new_events:
                yield f"data: {json.dumps({'type': 'done', 'status': status})}\n\n"
                return

            await asyncio.sleep(0.3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/jobs/docs-refresh")
async def create_docs_refresh_job(payload: DocsRefreshJobCreate) -> dict[str, Any]:
    try:
        app_doc_id = payload.app_doc_id
        if app_doc_id is None:
            if not payload.app_id:
                raise HTTPException(status_code=400, detail="Either app_doc_id or app_id is required")

            app_docs = database.list_docs(doc_type="dop_app", app_id=payload.app_id)
            if len(app_docs) == 0:
                raise HTTPException(status_code=404, detail=f"Application {payload.app_id} not found")

            app_doc_id = int(app_docs[0]["id"])

        return await jobs.create_docs_refresh_job(
            app_doc_id,
            payload.doc_type,
            depends_on_job_ids=payload.depends_on_job_ids,
            workflow_id=payload.workflow_id,
            max_parallel=payload.max_parallel,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/jobs/doc-action")
async def create_doc_action_job(payload: DocActionJobCreate) -> dict[str, Any]:
    try:
        return await jobs.create_doc_action_job(
            payload.doc_id,
            payload.action_name,
            depends_on_job_ids=payload.depends_on_job_ids,
            workflow_id=payload.workflow_id,
            max_parallel=payload.max_parallel,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/askpass/request")
async def create_askpass_request(payload: AskPassRequest) -> dict[str, Any]:
    """Create a password request (called by the job/container)."""
    try:
        return jobs.create_askpass_request(payload.job_id, payload.prompt)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/askpass/answer/{request_id}")
async def answer_askpass_request(request_id: str, payload: AskPassResponse) -> dict[str, bool]:
    """Answer an askpass request (called by the UI after user enters password)."""
    try:
        success = jobs.answer_askpass_request(request_id, payload.password, payload.save)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not success:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    return {"answered": True}


@app.post("/api/askpass/cancel/{request_id}")
async def cancel_askpass_request(request_id: str) -> dict[str, bool]:
    """Cancel an askpass request (called by UI Cancel)."""
    success = jobs.cancel_askpass_request(request_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    return {"answered": True}


@app.get("/api/askpass/answer/{request_id}")
async def get_askpass_answer(request_id: str) -> dict[str, str | None]:
    """Poll for the answer to an askpass request (called by the script in container)."""
    answer = jobs.get_askpass_answer(request_id)
    return {"answer": answer}


@app.get("/api/jobs/{job_id}/askpass")
def get_job_askpass_requests(job_id: str) -> list[dict[str, Any]]:
    """Get pending askpass requests for a job."""
    return jobs.get_pending_askpass_requests(job_id)


@app.get("/api/applications")
def list_applications() -> list[dict[str, Any]]:
    return [doc.to_dict() for doc in database.list_docs(doc_type="dop_app")]


@app.post("/api/applications/test")
def test_application(payload: ApplicationTest) -> dict[str, str]:
    plugin_key = payload.plugin_key.strip()
    if not plugin_key:
        raise HTTPException(status_code=400, detail="plugin_key is required")
    return _run_application_test(plugin_key, payload.app_id, payload.settings)


@app.post("/api/applications")
def add_application(payload: ApplicationCreate) -> dict[str, Any]:
    app_config = registry.get_app_config(payload.plugin_key)
    if app_config is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin app: {payload.plugin_key}")

    if app_config.get("uniq"):
        existing_apps = database.list_docs(doc_type="dop_app")
        already_exists = any(
            (doc.get("content") or {}).get("plugin_key") == payload.plugin_key
            for doc in existing_apps
        )
        if already_exists:
            raise HTTPException(status_code=400, detail=f"Application {payload.plugin_key} can be added only once")

    for setting_key, definition in app_config.get("settings", {}).items():
        if definition.get("mandatory") and not payload.settings.get(setting_key):
            raise HTTPException(status_code=400, detail=f"Mandatory setting is required: {setting_key}")

    content = {
        "plugin_key": payload.plugin_key,
        "name": app_config.get("name"),
        "description": app_config.get("description"),
        "description_long": app_config.get("description_long"),
        "icon": app_config.get("icon"),
        "settings": payload.settings,
        "doc_types": app_config.get("doc_types", []),
    }
    created = database.add_doc(DopDoc(app_id=payload.app_id, doc_type="dop_app", content=content)).to_dict()
    sync_app_yaml_agent_profiles()
    return created


@app.get("/api/applications/{app_doc_id}")
def get_application(app_doc_id: int) -> dict[str, Any]:
    try:
        doc = database.get_doc(app_doc_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if doc.get("doc_type") != "dop_app":
        raise HTTPException(status_code=404, detail=f"Application {app_doc_id} not found")

    return doc.to_dict()


@app.put("/api/applications/{app_doc_id}")
def update_application(app_doc_id: int, payload: ApplicationUpdate) -> dict[str, Any]:
    try:
        existing = database.get_doc(app_doc_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if existing.get("doc_type") != "dop_app":
        raise HTTPException(status_code=404, detail=f"Application {app_doc_id} not found")

    content = existing.get("content", {})
    incoming_content = dict(payload.content) if isinstance(payload.content, dict) else {}
    if payload.settings is not None:
        incoming_content["settings"] = payload.settings
    if payload.description is not None:
        incoming_content["description"] = payload.description
    if payload.url is not None:
        incoming_content["url"] = payload.url

    plugin_key = content.get("plugin_key")
    app_config = registry.get_app_config(plugin_key)
    if app_config is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin app: {plugin_key}")

    next_settings = incoming_content.get("settings", content.get("settings", {}))
    if not isinstance(next_settings, dict):
        raise HTTPException(status_code=400, detail="settings must be an object")

    for setting_key, definition in app_config.get("settings", {}).items():
        if definition.get("mandatory") and not next_settings.get(setting_key):
            raise HTTPException(status_code=400, detail=f"Mandatory setting is required: {setting_key}")

    updated_content = {
        **content,
        **incoming_content,
        "name": app_config.get("name"),
        "description": incoming_content.get("description", content.get("description", app_config.get("description"))),
        "description_long": app_config.get("description_long"),
        "icon": app_config.get("icon"),
        "url": incoming_content.get("url", content.get("url")),
        "settings": next_settings,
        "doc_types": app_config.get("doc_types", []),
    }

    try:
        updated = database.update_doc(
            app_doc_id,
            DopDoc(app_id=existing.get("app_id"), doc_type=existing.get("doc_type"), content=updated_content),
        ).to_dict()
        sync_app_yaml_agent_profiles()
        return updated
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/api/applications/{app_doc_id}")
def delete_application(app_doc_id: int) -> dict[str, bool]:
    try:
        doc = database.get_doc(app_doc_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if doc.get("doc_type") != "dop_app":
        raise HTTPException(status_code=404, detail=f"Application {app_doc_id} not found")

    if doc.get("app_id") == "devops-pass-ai":
        raise HTTPException(status_code=400, detail="Cannot delete built-in application")

    app_id = doc.get("app_id")
    deleted_count = database.delete_docs_by_app_id(app_id)
    sync_app_yaml_agent_profiles()
    return {"deleted": deleted_count > 0}


@app.get("/api/products")
def list_products() -> list[dict[str, Any]]:
    docs_cache: dict[tuple[str, str], list[DopDoc]] = {}
    legacy_doc_cache: dict[int, DopDoc] = {}
    facts_cache: dict[tuple[str, str, str, str], bool] = {}
    return [
        _product_to_dict(doc, docs_cache=docs_cache, legacy_doc_cache=legacy_doc_cache, facts_cache=facts_cache)
        for doc in database.list_docs(doc_type="dop_product", include_facts=False)
    ]


@app.post("/api/products")
def add_product(payload: ProductCreate) -> dict[str, Any]:
    product_id = payload.product_id.strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    existing = database.list_docs(doc_type="dop_product", app_id=product_id)
    if existing:
        raise HTTPException(status_code=400, detail=f"Product {product_id} already exists")

    prompt = (payload.prompt or "").strip()
    description = (payload.description or "").strip() or prompt
    content = {
        "name": name,
        "description": description,
        "prompt": prompt,
        "icon": payload.icon,
        "url": payload.url,
        "resources": normalize_doc_refs(database, [resource.model_dump() for resource in payload.resources]),
    }

    created = database.add_doc(DopDoc(app_id=product_id, doc_type="dop_product", content=content))
    return _product_to_dict(created, facts_cache={})


@app.get("/api/products/{product_doc_id}")
def get_product(product_doc_id: int) -> dict[str, Any]:
    try:
        doc = database.get_doc(product_doc_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if doc.get("doc_type") != "dop_product":
        raise HTTPException(status_code=404, detail=f"Product {product_doc_id} not found")

    return _product_to_dict(doc, facts_cache={})


@app.put("/api/products/{product_doc_id}")
def update_product(product_doc_id: int, payload: ProductUpdate) -> dict[str, Any]:
    try:
        existing = database.get_doc(product_doc_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if existing.get("doc_type") != "dop_product":
        raise HTTPException(status_code=404, detail=f"Product {product_doc_id} not found")

    content = existing.get("content", {})

    name = payload.name.strip() if payload.name is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=400, detail="name cannot be empty")

    prompt = payload.prompt.strip() if payload.prompt is not None else None
    description = payload.description.strip() if payload.description is not None else None

    updated_content = {
        **content,
        **({"name": name} if name is not None else {}),
        **({"prompt": prompt} if prompt is not None else {}),
        **({"description": description} if description is not None else {}),
        **({"icon": payload.icon} if payload.icon is not None else {}),
        **({"url": payload.url} if payload.url is not None else {}),
        **(
            {"resources": normalize_doc_refs(database, [resource.model_dump() for resource in payload.resources])}
            if payload.resources is not None
            else {}
        ),
    }

    if "resources" in updated_content:
        updated_content["resources"] = normalize_doc_refs(database, updated_content.get("resources") or [])

    if "description" not in updated_content or not updated_content.get("description"):
        if "prompt" in updated_content and updated_content.get("prompt"):
            updated_content["description"] = updated_content["prompt"]

    try:
        updated = database.update_doc(
            product_doc_id,
            DopDoc(app_id=existing.get("app_id"), doc_type=existing.get("doc_type"), content=updated_content),
        )
        return _product_to_dict(updated, facts_cache={})
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/api/products/{product_doc_id}")
def delete_product(product_doc_id: int) -> dict[str, bool]:
    try:
        doc = database.get_doc(product_doc_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if doc.get("doc_type") != "dop_product":
        raise HTTPException(status_code=404, detail=f"Product {product_doc_id} not found")

    deleted = database.delete_doc(product_doc_id)
    return {"deleted": deleted}


@app.get("/api/docs")
def search_docs(
    q: str | None = Query(default=None),
    doc_type: str | None = Query(default=None),
    app_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    all_docs = search_docs_by_query(database, query=q, doc_type=doc_type, app_id=app_id)
    total = len(all_docs)
    paginated_docs = all_docs[offset : offset + limit]
    return {
        "results": [doc.to_dict() for doc in paginated_docs],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: int) -> dict[str, Any]:
    try:
        return database.get_doc(doc_id).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/docs")
def add_doc(payload: DocCreate) -> dict[str, Any]:
    return database.add_doc(
        DopDoc(app_id=payload.app_id, doc_type=payload.doc_type, content=payload.content)
    ).to_dict()
