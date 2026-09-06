from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from app.api.v1 import crawls, documents, domains, health, intelligence, preflight, search, sources
from app.core.config import apply_hf_token_to_environ, get_settings
from app.core.errors import APIError, api_error_handler, unhandled_exception_handler
from app.core.logging import configure_logging, request_id_var
from app.core.metrics import register_metric_subscribers
from app.services.events import default_bus
from app.services.scheduling.scheduler import scheduler_loop
from app.services.security.url_security import URLSecurityError

configure_logging()
# Make HF_TOKEN from .env visible to huggingface_hub before any model load.
apply_hf_token_to_environ()


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_hf_token_to_environ()
    register_metric_subscribers(default_bus)
    task = asyncio.create_task(scheduler_loop(get_settings()))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Web Intelligence Collection Backend",
    version="0.1.0",
    description="Pre-flight assessment, instant crawl, and continuous monitoring for public web sources.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.exception_handler(URLSecurityError)
async def url_security_error_handler(request: Request, exc: URLSecurityError):
    return await api_error_handler(
        request, APIError(code="URL_BLOCKED", message=str(exc), status_code=400)
    )


app.include_router(health.router)
app.include_router(preflight.router, prefix="/api/v1")
app.include_router(crawls.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(sources.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(domains.router, prefix="/api/v1")
app.include_router(intelligence.router, prefix="/api/v1")
