"""FastAPI app for the Knowledge Brain.  Run:  uvicorn task1_knowledge_brain.app.api:app --port 8000"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from task1_knowledge_brain.app import escalations
from task1_knowledge_brain.app.schemas import (
    Escalation, EscalationCreate, EscalationResolve, IngestionRunRequest, QueryRequest, QueryResponse,
    SearchRequest, SearchResult,
)
from task1_knowledge_brain.service import (
    IngestionBusy, KnowledgeService, LLMError, LLMUnavailable, build_service,
)

log = logging.getLogger(__name__)
QUERY_TIMEOUT_S = 60

_service: KnowledgeService | None = None


def get_service() -> KnowledgeService:
    global _service
    if _service is None:
        _service = build_service()
    return _service


def set_service(service: KnowledgeService | None) -> None:
    """Used by tests to inject a service with fake dependencies."""
    global _service
    _service = service


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if os.getenv("KB_DAILY_SYNC", "1") == "1" and not os.getenv("PYTEST_CURRENT_TEST"):
        from task1_knowledge_brain.app.scheduler import start_scheduler

        scheduler = start_scheduler(get_service)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Knowledge Brain API",
    version="1.0.0",
    description="Grounded answers with citations over the Brilliant Directories help center. See API.md.",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_service().settings.api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header.")


@app.get("/health")
def health() -> dict:
    s = get_service()
    status = s.status()
    return {
        "status": "ok",
        "articles_active": status["articles_active"],
        "chunks_active": status["chunks_active"],
        "llm_configured": s.llm is not None,
        "llm_provider": s.llm.provider if s.llm else None,
        "ingestion_running": status["running"],
    }


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
async def query(req: QueryRequest) -> QueryResponse:
    s = get_service()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(s.answer, req.question, top_k=req.top_k, requester=req.requester,
                              escalate=req.escalate, model=req.model),
            timeout=QUERY_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Answer generation timed out.")
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Model call failed: {exc}")


@app.post("/search", response_model=list[SearchResult], dependencies=[Depends(require_api_key)])
def search(req: SearchRequest) -> list[SearchResult]:
    hits = get_service().search(req.query, k=req.k, mode=req.mode)
    return [SearchResult(chunk_id=h.chunk_id, title=h.title, url=h.url, section=h.section,
                         source_type=h.source_type, text=h.text, image_urls=h.image_urls,
                         similarity=round(h.similarity, 4)) for h in hits]


@app.post("/ingestion/run", status_code=202, dependencies=[Depends(require_api_key)])
async def ingestion_run(req: IngestionRunRequest, background: BackgroundTasks) -> dict:
    s = get_service()
    if s.ingestion_running:
        raise HTTPException(status_code=409, detail="An ingestion run is already in progress.")

    async def job() -> None:
        try:
            await s.run_ingestion(mode=req.mode, limit=req.limit, describe_images=req.describe_images)
        except IngestionBusy:
            pass
        except Exception:
            log.exception("Ingestion run failed")

    background.add_task(job)
    return {"accepted": True, "mode": req.mode, "limit": req.limit}


@app.get("/ingestion/status", dependencies=[Depends(require_api_key)])
def ingestion_status() -> dict:
    return get_service().status()


@app.get("/escalations", response_model=list[Escalation], dependencies=[Depends(require_api_key)])
def list_escalations(status: str | None = None) -> list[dict]:
    if status not in (None, "open", "resolved"):
        raise HTTPException(status_code=422, detail="status must be 'open' or 'resolved'")
    return escalations.list_all(get_service().conn, status)


@app.post("/escalations", response_model=Escalation, status_code=201, dependencies=[Depends(require_api_key)])
def create_escalation(req: EscalationCreate) -> dict:
    conn = get_service().conn
    esc_id = escalations.create(conn, req.question, req.reason, [], req.requester)
    return escalations.get(conn, esc_id)


@app.post("/escalations/{escalation_id}/resolve", response_model=Escalation,
          dependencies=[Depends(require_api_key)])
def resolve_escalation(escalation_id: int, req: EscalationResolve) -> dict:
    s = get_service()
    try:
        return escalations.resolve(s.conn, s.index, escalation_id, answer=req.answer, expert=req.expert,
                                   scope=req.scope, source_url=req.source_url, expires_at=req.expires_at)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Escalation {escalation_id} not found.")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error. See server logs."})
