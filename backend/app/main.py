import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.middleware.gzip import GZipMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.security.middleware import (
    ExpensiveRequestRateLimitMiddleware,
    SecurityHeadersMiddleware,
)


logger = logging.getLogger(__name__)


def _warm_rag_embedding() -> None:
    """Pay the local E5 load and first-inference cost before passenger traffic."""

    from app.rag.embeddings import get_embedder

    get_embedder().embed_query("Kenya Airways policy information")


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    """Prepare reusable local resources and close provider connections cleanly."""

    settings = get_settings()
    if settings.rag_warmup_on_startup:
        try:
            await asyncio.to_thread(_warm_rag_embedding)
            logger.info("RAG embedding model warm-up completed.")
        except Exception as error:
            # A warm-up failure must not hide the health endpoint. The normal
            # retrieval path will still return its existing diagnostic error.
            logger.warning(
                "RAG embedding warm-up was unavailable (%s).",
                type(error).__name__,
            )
    yield

    from app.rag.generation import get_generation_service

    if get_generation_service.cache_info().currsize:
        close_client = getattr(
            get_generation_service().client,
            "close",
            None,
        )
        if callable(close_client):
            close_client()
        get_generation_service.cache_clear()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Backend API for the airline multilingual RAG customer support chatbot.",
        version=settings.app_version,
        debug=settings.debug,
        lifespan=application_lifespan,
    )

    # Compress larger API payloads in deployment. Tables, conversations and
    # analytics transfer fewer bytes without changing their response shapes.
    app.add_middleware(
        GZipMiddleware,
        minimum_size=1000,
    )
    app.add_middleware(
        ExpensiveRequestRateLimitMiddleware,
        requests_per_minute=settings.expensive_requests_per_minute,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    if settings.cors_origins:
        development_origin_regex = (
            r"https?://(localhost|127\.0\.0\.1):[0-9]+"
            if settings.environment.lower() == "development"
            else None
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            # Vite may move from 5173 to another local port when that port is busy.
            # Restrict the regex to loopback development hosts; production origins
            # still come from the explicit CORS_ORIGINS setting above.
            allow_origin_regex=development_origin_regex,
            allow_credentials=True,
            allow_methods=[
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "OPTIONS",
            ],
            allow_headers=[
                "Authorization",
                "Content-Type",
            ],
        )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # Convert unexpected route failures into responses that still pass through
        # CORS, so the browser receives the real API error instead of a CORS mask.
        logger.exception("Unhandled API error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "The KQ Support API encountered an unexpected error."},
        )

    @app.get("/")
    def root():
        return {
            "message": "KQ Customer Support Chatbot API is running",
            "environment": settings.environment,
        }

    @app.get("/health")
    def health_check():
        return {
            "status": "healthy"
        }

    return app



# Keep an outer CORS layer around the production ASGI app. Starlette's inner
# middleware cannot add CORS headers when an unexpected exception escapes all
# route handlers, which otherwise appears in browsers as a misleading CORS error.
_fastapi_app = create_app()
_settings = get_settings()
app = CORSMiddleware(
    _fastapi_app,
    allow_origins=_settings.cors_origins,
    allow_origin_regex=(
        r"https?://(localhost|127\.0\.0\.1):[0-9]+"
        if _settings.environment.lower() == "development"
        else None
    ),
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
    ],
)
