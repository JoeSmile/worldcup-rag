from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import chat
from core.chat_response import enrich_chat_result
from core.chat_validation import validate_history_session_conflict
from core.logger import bind_trace_id, get_logger, log_extra, new_trace_id
from core.memory import get_session_memory
from core.observability import init_observability
from core.query_cache import get_query_cache
from core.session_id import normalize_session_id, validate_session_id
from etl.data.db import execute_query

logger = get_logger("app")

app = FastAPI(title="World Cup RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    init_observability()
    logger.info("application started")


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    history: Optional[List[Dict[str, str]]] = None
    session_id: Optional[str] = Field(default=None, max_length=128)
    skip_cache: bool = False


class ChatResponse(BaseModel):
    answer: str
    tool_name: Optional[str] = None
    tools_used: Optional[List[str]] = None
    sql_generated: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    workflow: Optional[str] = None
    router_choice: Optional[str] = None
    auto_routed: Optional[bool] = None
    router_method: Optional[str] = None
    router_confidence: Optional[float] = None
    router_reason: Optional[str] = None
    cache_hit: Optional[bool] = None
    cache_layer: Optional[str] = None
    session_id: Optional[str] = None
    memory_persisted: Optional[bool] = None
    post_chat_scheduled: Optional[Dict[str, bool]] = None


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    header_trace = request.headers.get("X-Trace-Id")
    trace_id = bind_trace_id(header_trace or new_trace_id())
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, http_request: Request):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query cannot be empty")

    session_id = normalize_session_id(request.session_id)
    if session_id == "":
        raise HTTPException(status_code=400, detail="session_id cannot be empty")
    if session_id is not None:
        try:
            session_id = validate_session_id(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        validate_history_session_conflict(session_id=session_id, history=request.history)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace_id = getattr(http_request.state, "trace_id", None) or bind_trace_id(None)
    logger.info(
        "chat request",
        extra=log_extra(
            trace_id=trace_id,
            query_len=len(query),
            has_history=bool(request.history),
            session_id=session_id,
        ),
    )

    try:
        result = enrich_chat_result(
            await run_in_threadpool(
                chat,
                query,
                request.history,
                None,
                trace_id,
                request.skip_cache,
                session_id,
            ),
            session_id,
        )
        logger.info(
            "chat completed",
            extra=log_extra(
                trace_id=trace_id,
                workflow=result.get("workflow"),
                router_choice=result.get("router_choice"),
                router_method=result.get("router_method"),
                tools_used=result.get("tools_used"),
                cache_layer=result.get("cache_layer"),
                memory_persisted=result.get("memory_persisted"),
            ),
        )
        return result
    except Exception as exc:
        logger.exception(
            "chat request failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    try:
        result = await run_in_threadpool(execute_query, "SELECT 1")
    except Exception as exc:
        logger.exception("database readiness check failed", extra=log_extra())
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready", "database": result[0][0] == 1}


@app.get("/cache/stats")
async def cache_stats():
    return get_query_cache().get_stats()


@app.post("/cache/clear")
async def cache_clear():
    removed = await run_in_threadpool(get_query_cache().clear)
    return {"status": "ok", "removed": removed}


def _validated_session_id(raw: str) -> str:
    session_id = validate_session_id(normalize_session_id(raw) or "")
    return session_id


@app.get("/session/{session_id}/stats")
async def session_stats(session_id: str):
    try:
        session_id = _validated_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stats = await run_in_threadpool(get_session_memory().get_stats, session_id)
    return {"session_id": session_id, **stats}


@app.delete("/session/{session_id}")
async def session_clear(session_id: str):
    try:
        session_id = _validated_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    removed = await run_in_threadpool(get_session_memory().clear, session_id)
    return {"status": "ok", "session_id": session_id, "removed_keys": removed}


if __name__ == "__main__":
    import uvicorn

    from core.config import settings

    uvicorn.run(app, host=settings.app_host, port=settings.app_port)
