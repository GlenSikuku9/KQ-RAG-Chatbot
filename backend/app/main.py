from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings


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


app = create_app()