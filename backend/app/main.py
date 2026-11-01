import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.router import api_router
from app.config import get_settings


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Backend API for the airline multilingual RAG customer support chatbot.",
        version=settings.app_version,
        debug=settings.debug,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
