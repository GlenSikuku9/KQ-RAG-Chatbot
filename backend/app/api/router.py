from fastapi import APIRouter

from app.api.routes import auth, conversations, generation, health, models, retrieval


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(generation.router, prefix="/generation", tags=["generation"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
