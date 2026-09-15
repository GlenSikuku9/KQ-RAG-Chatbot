from typing import Any

from fastapi import HTTPException, status

from app.config import get_settings
from app.models.user import AuthenticatedUser


def _get_firebase_app() -> Any:
    """Initialize Firebase Admin lazily so app startup does not require credentials immediately."""

    from firebase_admin import credentials, get_app, initialize_app

    settings = get_settings()

    try:
        return get_app()
    except ValueError:
        # Firebase Admin raises ValueError when no default app has been created yet.
        pass

    options: dict[str, str] = {}
    if settings.firestore_project_id:
        options["projectId"] = settings.firestore_project_id

    if settings.firebase_credentials_path:
        if not settings.firebase_credentials_path.exists():
            raise RuntimeError(
                f"Firebase credentials file was not found: {settings.firebase_credentials_path}"
            )

        credential = credentials.Certificate(str(settings.firebase_credentials_path))
        return initialize_app(credential, options=options)

    # Without an explicit service-account file, Firebase Admin will use Application
    # Default Credentials. This keeps secrets out of source code and .env files.
    return initialize_app(options=options)


def verify_firebase_id_token(id_token: str) -> AuthenticatedUser:
    """Verify a Firebase ID token and return a role-aware application user."""

    from firebase_admin import auth

    _get_firebase_app()

    try:
        decoded_token = auth.verify_id_token(id_token, check_revoked=True)
        return AuthenticatedUser.from_firebase_claims(decoded_token)
    except auth.ExpiredIdTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except auth.RevokedIdTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has been revoked. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except auth.InvalidIdTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated user has an unsupported or missing role.",
        ) from exc
