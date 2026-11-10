from fastapi import APIRouter

from app.api.routes import (
    admin,
    auth,
    conversations,
    feedback,
    flights,
    generation,
    guest,
    health,
    models,
    retrieval,
    support,
    theme,
)


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(generation.router, prefix="/generation", tags=["generation"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(flights.router, prefix="/flights", tags=["flight-information"])
api_router.include_router(support.router, prefix="/support", tags=["human-support"])
api_router.include_router(admin.router, prefix="/admin", tags=["administrator"])
api_router.include_router(theme.router, tags=["theme"])
api_router.include_router(guest.router, tags=["guest-chat"])
