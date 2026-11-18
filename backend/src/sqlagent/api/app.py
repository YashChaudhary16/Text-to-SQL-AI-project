"""HTTP API: streams each agent step to the UI over Server-Sent Events.

POST /api/chat     {"thread_id"?: str, "message": str}  -> text/event-stream
    If the thread is paused on a clarifying question, `message` is the user's answer and the
    graph resumes; otherwise it starts a new turn. Optional header `X-Anthropic-Key` (BYO key)
    routes this request through Claude; the key is never stored or logged.
GET  /api/schema   tables for the data-explorer sidebar
GET  /api/health   liveness + which LLM providers are configured
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from sqlagent.config import get_settings
from sqlagent.db.warehouse import Warehouse
from sqlagent.graph.agent import Deps, build_graph
from sqlagent.graph.state import RunContext, new_turn
from sqlagent.llm import AllProvidersFailedError, LLMRouter
from sqlagent.retrieval import SchemaRetriever
from sqlagent.semantic.catalog import Catalog

log = logging.getLogger("sqlagent.api")


class ChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    catalog = Catalog.load(s.catalog_path)
    warehouse = Warehouse(s, {t.name for t in catalog.tables})
    deps = Deps(
        settings=s,
        catalog=catalog,
        retriever=SchemaRetriever(catalog, s.retrieval_top_tables),
        warehouse=warehouse,
        llm=LLMRouter(s),
    )
    async with AsyncExitStack() as stack:
        if s.database_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            checkpointer = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(s.database_url)
            )
            await checkpointer.setup()
        else:
            log.warning("DATABASE_URL not set: conversations are kept in memory only")
            checkpointer = InMemorySaver()
        app.state.deps = deps
        app.state.graph = build_graph(deps, checkpointer)
        yield
    warehouse.close()


app = FastAPI(title="Text-to-SQL Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Anthropic-Key"],
)


# --- rate limiting ------------------------------------------------------------------------
# Per-process sliding window. Good enough for one instance; serverless needs a shared store.
_hits: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(request: Request) -> None:
    limit = get_settings().rate_limit_per_minute
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.monotonic()
    window = _hits[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(429, "Rate limit reached. Wait a minute, or add your own Anthropic key.")
    window.append(now)


# --- routes -------------------------------------------------------------------------------
@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    llm: LLMRouter = request.app.state.deps.llm
    return {
        "status": "ok",
        "models": {"router": llm.available_models("router"), "sql": llm.available_models("sql")},
    }


@app.get("/api/schema")
async def schema(request: Request) -> dict[str, Any]:
    catalog: Catalog = request.app.state.deps.catalog
    return {
        "domain": catalog.domain.strip(),
        "coverage": {k: v.model_dump() for k, v in catalog.coverage.items()},
        "tables": [
            {
                "name": t.name,
                "business_name": t.business_name,
                "kind": t.kind,
                "description": t.description,
                "row_count": t.row_count,
                "columns": [
                    {"name": c.name, "type": c.type, "description": c.description} for c in t.columns
                ],
            }
            for t in catalog.tables
        ],
    }


@app.post("/api/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    x_anthropic_key: str | None = Header(default=None),
) -> EventSourceResponse:
    byo_key = (x_anthropic_key or "").strip() or None
    if byo_key and not byo_key.startswith("sk-ant-"):
        raise HTTPException(400, "That doesn't look like an Anthropic API key.")
    if not byo_key:
        _check_rate_limit(request)

    graph = request.app.state.graph
    thread_id = body.thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    resuming = bool(snapshot and snapshot.interrupts)
    graph_input = Command(resume=body.message) if resuming else new_turn(body.message)

    return EventSourceResponse(
        _stream(graph, graph_input, config, RunContext(anthropic_api_key=byo_key), thread_id)
    )


async def _stream(graph, graph_input, config, context: RunContext, thread_id: str) -> AsyncIterator[dict]:
    yield _event("thread", {"thread_id": thread_id})
    try:
        async for mode, chunk in graph.astream(
            graph_input, config, context=context, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":  # e.g. {"type": "fallback", "model": ..., "reason": ...}
                yield _event("notice", chunk)
                continue
            for node, update in chunk.items():
                if node == "__interrupt__":
                    payload = update[0].value
                    yield _event(
                        "clarification", {"question": payload["question"], "options": payload["options"]}
                    )
                    continue
                for event in _events_for(node, update or {}):
                    yield event
    except AllProvidersFailedError as e:
        log.error("LLM providers exhausted: %s", e)
        yield _event(
            "error",
            {
                "message": "All AI providers are busy or unavailable. "
                "Try again shortly, or add your own Anthropic key."
            },
        )
    except Exception:
        log.exception("chat stream failed")
        yield _event("error", {"message": "Something went wrong while answering. Please try again."})
    yield _event("done", {})


def _events_for(node: str, u: dict[str, Any]) -> list[dict]:
    """Translate a node's state update into UI events. Only safe, user-facing fields."""
    if node == "understand":
        return [
            _event(
                "step",
                {
                    "node": node,
                    "intent": u.get("intent"),
                    "question": u.get("standalone_question"),
                    "assumptions": u.get("assumptions", []),
                    "model": _last_model(u),
                },
            )
        ]
    if node == "retrieve":
        return [_event("step", {"node": node, "tables": u.get("tables", [])})]
    if node == "write_sql":
        return [_event("step", {"node": node, "sql": u.get("sql"), "model": _last_model(u)})]
    if node == "execute":
        if u.get("result") is not None:
            return [_event("result", {"sql": u.get("sql"), **u["result"]})]
        return [_event("step", {"node": node, "error": u.get("last_error"), "attempt": u.get("attempts")})]
    if node == "clarify":
        return [_event("step", {"node": node})]
    if node in ("respond", "explain_schema", "out_of_scope", "give_up"):
        return [_event("answer", {"text": u.get("answer"), "llm_calls": u.get("llm_calls", [])})]
    return []


def _last_model(u: dict[str, Any]) -> str | None:
    calls = u.get("llm_calls") or []
    return calls[-1]["model"] if calls else None


def _event(name: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data, default=str)}
