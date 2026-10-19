"""Admin-only provider configuration inspection and connectivity probe."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_admin
from app.models.generation import ConfiguredModel, GenerationRequest, GenerationResponse
from app.models.user import AuthenticatedUser
from app.rag.generation import GenerationProviderError, get_generation_service

router = APIRouter()


@router.get("", response_model=list[ConfiguredModel])
def list_models(current_user: AuthenticatedUser = Depends(require_admin)) -> list[ConfiguredModel]:
    """List safe metadata only: API keys are never returned."""
    return get_generation_service().configured_models()


@router.post("/generate", response_model=GenerationResponse)
def probe_model(payload: GenerationRequest, current_user: AuthenticatedUser = Depends(require_admin)) -> GenerationResponse:
    """Send an explicit admin test request; no automatic model fallback occurs."""
    try:
        return get_generation_service().generate(payload.prompt, payload.model_id, payload.system_instruction)
    except GenerationProviderError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
