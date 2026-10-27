from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import Settings, apply_env_file, load_settings
from app.logging_config import RunTrace, configure_stdout_logging
from app.pipeline import Investigation
from app.run_store import RunStore
from app.schemas import RUN_ID_PATTERN, ErrorBody, ErrorResponse, InvestigateRequest, InvestigateResponse
from app.services import reporting
from app.services.http_client import Budget, ProviderError
from app.services.meterless_engine import HMemClient
from app.services.nightshift import NightshiftClient
from app.services.zetaris import ZetarisClient

HARD_LIMIT_GRACE_SECONDS = 5.0


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False, run_id: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.run_id = run_id


def _trace_id(request: Request) -> str:
    if not hasattr(request.state, "trace_id"):
        request.state.trace_id = uuid.uuid4().hex
    return request.state.trace_id


def _error(request: Request, status: int, code: str, message: str, retryable: bool = False, run_id: str | None = None, details: list[dict[str, Any]] | None = None) -> JSONResponse:
    body = ErrorResponse(run_id=run_id, error=ErrorBody(code=code, message=message, retryable=retryable, details=details or []), trace_id=_trace_id(request))
    return JSONResponse(status_code=status, content=body.model_dump(), headers={"X-Trace-ID": body.trace_id})


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_stdout_logging()
    store = RunStore(settings.log_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="VERITAS — Cognitive Bias & Deepfake Forensic Suite",
        version=__version__,
        description="Evidence-backed claim investigation with a provenance graph. Sample mode runs fully offline on fictional data.",
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    app.state.settings = settings
    app.state.store = store

    @app.middleware("http")
    async def guard(request: Request, call_next):
        trace_id = _trace_id(request)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_content_chars * 4 + 64_000:
            return _error(request, 413, "payload_too_large", "Request body is too large.")
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _error(request, exc.status, exc.code, exc.message, exc.retryable, exc.run_id)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))} for e in exc.errors()]
        return _error(request, 422, "validation_error", "The request body is invalid.", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        return _error(request, exc.status_code, codes.get(exc.status_code, f"http_{exc.status_code}"), str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        RunTrace("server", _trace_id(request), settings.log_dir, settings.secret_values()).emit("api", "unhandled_error", "failed", error_type=type(exc).__name__)
        return _error(request, 500, "internal_error", "An internal error occurred. Use the trace_id to find the log entry.", retryable=True)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "veritas",
            "version": __version__,
            "sample_mode_default": settings.sample_mode,
            "integrations_configured": {
                "zetaris": settings.zetaris_configured,
                "meterless_nightshift": settings.nightshift_configured,
                "meterless_hmem": settings.hmem_configured,
                "nvidia": settings.nvidia_configured,
                "media_detector": False,
            },
        }

    @app.get("/", response_class=HTMLResponse, tags=["system"])
    async def index() -> str:
        return _INDEX_HTML.replace("{version}", __version__)

    @app.post("/api/v1/investigate", response_model=InvestigateResponse, tags=["investigation"])
    async def investigate(body: InvestigateRequest, request: Request) -> InvestigateResponse:
        run_id = body.run_id or f"run-{uuid.uuid4().hex[:12]}"
        if not store.reserve(run_id):
            raise ApiError(409, "run_id_conflict", f"run_id '{run_id}' has already been used.", run_id=run_id)
        trace = RunTrace(run_id, _trace_id(request), settings.log_dir, settings.secret_values())
        investigation = Investigation(body, settings, run_id, trace, transport=transport)
        try:
            result = await asyncio.wait_for(investigation.run(), timeout=settings.budget_seconds + HARD_LIMIT_GRACE_SECONDS)
        except asyncio.TimeoutError:
            result = investigation.finalize(hard_timeout=True)
        except Exception as error:
            store.release(run_id)
            trace.emit("run", "failed", "failed", error_type=type(error).__name__)
            raise ApiError(500, "investigation_failed", "The investigation failed unexpectedly. Use the trace_id to find the log entry.", retryable=True, run_id=run_id) from error
        store.save(result)
        return result

    def _lookup(run_id: str) -> InvestigateResponse:
        if not RUN_ID_PATTERN.match(run_id):
            raise ApiError(400, "invalid_run_id", "run_id format is invalid.")
        result = store.get(run_id)
        if result is None:
            raise ApiError(404, "run_not_found", f"No run with id '{run_id}'.", run_id=run_id)
        return result

    @app.get("/api/v1/runs/{run_id}", response_model=InvestigateResponse, tags=["investigation"])
    async def get_run(run_id: str) -> InvestigateResponse:
        return _lookup(run_id)

    @app.get("/api/v1/runs/{run_id}/report.md", response_class=PlainTextResponse, tags=["investigation"])
    async def get_report(run_id: str) -> str:
        return reporting.render_markdown(_lookup(run_id))

    @app.delete("/api/v1/runs/{run_id}", tags=["investigation"])
    async def delete_run(run_id: str) -> dict[str, Any]:
        _lookup(run_id)
        store.delete(run_id)
        return {"deleted": run_id, "note": "Stored result removed. The JSONL audit log is kept; delete logs/<run_id>.jsonl to remove it."}

    @app.get("/api/v1/integrations/status", tags=["system"])
    async def integrations_status(request: Request) -> dict[str, Any]:
        """Live probes of each configured sponsor service. Never runs in sample mode."""
        if settings.sample_mode:
            return {"sample_mode": True, "note": "Sample mode: no live probes are made."}
        trace = RunTrace(f"status-{uuid.uuid4().hex[:8]}", _trace_id(request), settings.log_dir, settings.secret_values())
        budget = Budget(60.0, 1)
        out: dict[str, Any] = {}
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            if settings.nightshift_configured:
                out["meterless_nightshift"] = await NightshiftClient(settings, client, trace).status(budget)
            else:
                out["meterless_nightshift"] = "not_configured"
            if settings.zetaris_configured:
                try:
                    out["zetaris"] = "online" if await ZetarisClient(settings, client, trace).health(budget) else "no_rows"
                except (ProviderError, ValueError) as error:
                    out["zetaris"] = getattr(error, "code", "invalid_configuration")
            else:
                out["zetaris"] = "not_configured"
            if settings.hmem_configured:
                try:
                    out["meterless_hmem"] = (await HMemClient(settings, client, trace).health(budget)).get("status", "unknown")
                except ProviderError as error:
                    out["meterless_hmem"] = error.code
            else:
                out["meterless_hmem"] = "not_configured"
        out["nvidia"] = "configured_not_probed" if settings.nvidia_configured else "not_configured"
        out["trace_id"] = trace.trace_id
        return out

    return app


_INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>VERITAS</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#1f2933;background:#f7f8fa}
code,pre{background:#e9edf2;padding:2px 6px;border-radius:4px}pre{padding:12px;overflow:auto}a{color:#2f5d8a}</style></head>
<body><h1>VERITAS</h1><p>Cognitive Bias &amp; Deepfake Forensic Suite — API v{version}</p>
<ul><li><a href="/docs">Interactive API docs (Swagger)</a></li><li><a href="/health">Health</a></li></ul>
<p>Run the offline demonstration (fictional data, no network):</p>
<pre>curl -X POST http://localhost:8000/api/v1/investigate -H "Content-Type: application/json" \\
  -d '{"input":{"query":"Is the plant destroying the harbor?"},"options":{"sample_mode":true}}'</pre>
</body></html>"""

apply_env_file()
app = create_app()
