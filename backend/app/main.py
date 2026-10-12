import json
import os
import time
import uuid

import openai
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.auth import require_api_key
from app.conversations import resolve_conversation_id, get_history, add_turn, clear_conversation
from app.costs import estimate_cost_usd
from app.database import get_connection, fetch_schema
from app.knowledge_graph import tables_by_kind, get_graph
from app.query_engine import run_select, QueryError
from app.rate_limit import enforce_rate_limit
from app.agent import run_ask, run_ask_stream, AgentError, MODEL as AGENT_MODEL
from app.logger import log_ask, read_logs
from app import hydra_store
import app.upload as upload_module
from app.models import (
    ColumnInfo,
    TableInfo,
    SchemaResponse,
    GraphNode,
    GraphEdge,
    GraphResponse,
    QueryRequest,
    QueryResponse,
    AskRequest,
    AskResponse,
    UploadNoteRequest,
    UploadEnablementRequest,
    UploadResponse,
    LogsResponse,
    MemoryRequest,
    MemoriesResponse,
    FeedbackRequest,
)

app = FastAPI(title="Traceview API")

# FRONTEND_ORIGIN adds the deployed frontend's real origin on top of the
# regex below - unset locally, so dev behavior is unchanged; a deployment
# sets it to the actual prod URL (e.g. https://traceview.example.com).
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN else [],
    # Regex, not a fixed list - Vite falls back to the next free port
    # whenever something else is already on 5173 (as just happened), and a
    # hardcoded allowlist silently breaks every time that happens. Scoped
    # to localhost only, so this stays a dev convenience, not an open CORS
    # policy - FRONTEND_ORIGIN above is what actually opens it up in prod.
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/schema", response_model=SchemaResponse)
def get_schema():
    with get_connection() as conn:
        tables = fetch_schema(conn)

    kind_by_table = {}
    for kind, names in tables_by_kind().items():
        for name in names:
            kind_by_table[name] = kind

    return SchemaResponse(
        tables=[
            TableInfo(
                name=t["name"],
                columns=[ColumnInfo(name=c["name"], type=c["type"]) for c in t["columns"]],
                kind=kind_by_table.get(t["name"]),
            )
            for t in tables
        ]
    )


@app.get("/graph", response_model=GraphResponse)
def get_graph_structure():
    graph = get_graph()
    nodes = [
        GraphNode(id=name, kind=data.get("kind", "dimension"), description=data.get("description", ""))
        for name, data in graph.nodes(data=True)
    ]
    edges = [
        GraphEdge(source=u, target=v, label=data.get("label", "references"), via=data.get("via", ""))
        for u, v, data in graph.edges(data=True)
    ]
    return GraphResponse(nodes=nodes, edges=edges)


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
def query(request: QueryRequest):
    try:
        columns, rows, elapsed_ms, truncated = run_select(request.sql)
    except QueryError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return QueryResponse(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        execution_time_ms=elapsed_ms,
        truncated=truncated,
    )


@app.post("/upload/account-note", response_model=UploadResponse, dependencies=[Depends(require_api_key)])
def upload_account_note(request: UploadNoteRequest):
    try:
        result = upload_module.upload_account_note(request.account_id, request.content, request.author_role)
    except upload_module.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return UploadResponse(**result)


@app.post("/upload/enablement-content", response_model=UploadResponse, dependencies=[Depends(require_api_key)])
def upload_enablement_content(request: UploadEnablementRequest):
    try:
        result = upload_module.upload_enablement_content(request.title, request.category, request.content)
    except upload_module.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return UploadResponse(**result)


def _remember(user_id, result: dict) -> None:
    """
    Saves the fact a finished turn revealed about the user, if it
    revealed one (result["remembered"] - set by the agent's routing call,
    None for most turns). Shared by /ask and /ask/stream so both remember
    on exactly the same terms; off the request path, so it can't slow
    down or fail the answer it follows.
    """
    fact = result.get("remembered")
    if not user_id or not fact or not hydra_store.is_enabled():
        return
    hydra_store.remember_fact_async(user_id, fact)


def _openai_error_detail(exc: openai.OpenAIError) -> tuple[int, str]:
    """
    Maps an OpenAI SDK exception to (status_code, detail) - shared by /ask
    (raises HTTPException with the status code) and /ask/stream (only
    needs the detail text, for its "error" SSE event, since a streaming
    body can't change HTTP status after it's already started), so the
    same branches and message strings aren't kept in sync by hand across
    two call sites. AuthenticationError, RateLimitError, and
    APIStatusError are all subclasses of OpenAIError, so isinstance
    checks here do the same dispatch four separate except clauses used to.
    """
    if isinstance(exc, openai.AuthenticationError):
        return 500, "OPENAI_API_KEY is missing or invalid - set it in backend/.env"
    if isinstance(exc, openai.RateLimitError):
        return 429, "OpenAI API rate limit hit - try again shortly"
    if isinstance(exc, openai.APIStatusError):
        return 502, f"OpenAI API error: {exc.message}"
    return 500, f"OPENAI_API_KEY is missing or invalid - set it in backend/.env ({exc})"


@app.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def ask(request: AskRequest):
    # Generated before anything can fail, so every code path below - success
    # or any of the except branches - logs under the same ID, and the
    # client always gets one back even on a 4xx/5xx response.
    request_id = str(uuid.uuid4())
    start = time.perf_counter()
    conversation_id = resolve_conversation_id(request.conversation_id)
    history = get_history(conversation_id)

    try:
        result = run_ask(request.question, history=history, user_id=request.user_id)
    except AgentError as e:
        cost = estimate_cost_usd(AGENT_MODEL, e.prompt_tokens, e.completion_tokens)
        log_ask(
            request.question,
            (time.perf_counter() - start) * 1000,
            success=False,
            request_id=request_id,
            error=str(e),
            estimated_cost_usd=cost,
        )
        raise HTTPException(status_code=422, detail=str(e), headers={"X-Request-Id": request_id})
    except openai.OpenAIError as e:
        status_code, detail = _openai_error_detail(e)
        log_ask(request.question, (time.perf_counter() - start) * 1000, success=False, request_id=request_id, error=detail)
        raise HTTPException(status_code=status_code, detail=detail, headers={"X-Request-Id": request_id})

    add_turn(conversation_id, question=request.question, result=result)
    _remember(request.user_id, result)
    log_ask(request.question, (time.perf_counter() - start) * 1000, success=True, request_id=request_id, result=result)
    return AskResponse(**result, conversation_id=conversation_id, request_id=request_id)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post(
    "/ask/stream",
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def ask_stream(request: AskRequest):
    """
    Server-Sent Events version of /ask - same pipeline, same side effects
    (add_turn, log_ask), but the "unstructured"/"hybrid" answer text
    arrives as "delta" events instead of appearing all at once. Kept as a
    separate endpoint rather than replacing /ask, since a streaming body
    can't change HTTP status after it starts (the 200 + headers are
    already sent), so errors here are reported as an "error" event in the
    stream instead of an HTTPException status code - a real difference
    from /ask's contract, not just a style choice.
    """
    request_id = str(uuid.uuid4())
    start = time.perf_counter()
    conversation_id = resolve_conversation_id(request.conversation_id)
    history = get_history(conversation_id)

    def event_stream():
        def log_failure(detail):
            log_ask(request.question, (time.perf_counter() - start) * 1000, success=False, request_id=request_id, error=detail)

        try:
            for event in run_ask_stream(request.question, history=history, user_id=request.user_id):
                if event["type"] == "complete":
                    result = event["data"]
                    add_turn(conversation_id, question=request.question, result=result)
                    _remember(request.user_id, result)
                    log_ask(request.question, (time.perf_counter() - start) * 1000, success=True, request_id=request_id, result=result)
                    yield _sse({
                        "type": "complete",
                        "data": {**result, "conversation_id": conversation_id, "request_id": request_id},
                    })
                else:
                    yield _sse(event)
        except AgentError as e:
            cost = estimate_cost_usd(AGENT_MODEL, e.prompt_tokens, e.completion_tokens)
            log_ask(
                request.question,
                (time.perf_counter() - start) * 1000,
                success=False,
                request_id=request_id,
                error=str(e),
                estimated_cost_usd=cost,
            )
            yield _sse({"type": "error", "message": str(e), "request_id": request_id})
        except openai.OpenAIError as e:
            _, detail = _openai_error_detail(e)
            log_failure(detail)
            yield _sse({"type": "error", "message": detail, "request_id": request_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.delete("/conversations/{conversation_id}", dependencies=[Depends(require_api_key)])
def delete_conversation(conversation_id: str):
    clear_conversation(conversation_id)
    return {"status": "cleared"}


@app.get("/brain/status")
def brain_status():
    return hydra_store.status()


def _require_brain():
    if not hydra_store.is_enabled():
        raise HTTPException(status_code=503, detail="HydraDB isn't configured - set HYDRA_DB_API_KEY in backend/.env")


def _hydra_call(fn, *args):
    """
    Runs one HydraDB call for an endpoint whose whole job is that call
    (unlike /ask, which has pgvector to fall back on) - so a failure
    becomes a 502 naming what went wrong, not a bare 500.
    """
    _require_brain()
    try:
        return fn(*args)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HydraDB request failed ({type(e).__name__})")


@app.get("/memory", response_model=MemoriesResponse, dependencies=[Depends(require_api_key)])
def get_memories(user_id: str):
    return MemoriesResponse(user_id=user_id, memories=_hydra_call(hydra_store.list_memories, user_id))


@app.post("/memory", dependencies=[Depends(require_api_key)])
def add_memory(request: MemoryRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Memory text can't be empty.")
    memory_id = _hydra_call(hydra_store.remember_fact, request.user_id, request.text.strip())
    return {"id": memory_id, "status": "queued"}


@app.delete("/memory/{memory_id}", dependencies=[Depends(require_api_key)])
def delete_memory(memory_id: str, user_id: str):
    _hydra_call(hydra_store.forget_memory, user_id, memory_id)
    return {"status": "forgotten"}


@app.post("/feedback", dependencies=[Depends(require_api_key)])
def feedback(request: FeedbackRequest):
    if request.rating not in ("positive", "negative"):
        raise HTTPException(status_code=400, detail="rating must be 'positive' or 'negative'")
    _hydra_call(hydra_store.submit_feedback, request.hydra_request_id, request.rating, request.comment)
    return {"status": "recorded"}


@app.get("/logs", response_model=LogsResponse, dependencies=[Depends(require_api_key)])
def logs(limit: int = 50):
    return LogsResponse(entries=read_logs(limit))
