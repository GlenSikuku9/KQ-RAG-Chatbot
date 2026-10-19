from fastapi import APIRouter

from app.api.routes import auth, health, models, retrieval


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
