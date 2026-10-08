import logging
from threading import Lock

from fastapi import HTTPException, status
from firebase_admin import App, auth, credentials, exceptions, get_app, initialize_app
from google.auth.exceptions import GoogleAuthError

from app.config import get_settings
from app.models.user import AuthenticatedUser, UnsupportedUserRoleError


logger = logging.getLogger(__name__)
_initialization_lock = Lock()


class FirebaseConfigurationError(RuntimeError):
    """Firebase cannot initialize with the configured project and credentials."""


def get_firebase_app() -> App:
    """Reuse a single SDK app; unrelated endpoints can start without credentials."""

    # FastAPI can run multiple authentication requests in worker threads at once.
    with _initialization_lock:
        try:
            return get_app()
        except ValueError:
            # The SDK raises ValueError when the default app has not been created.
            pass

        settings = get_settings()
        if not settings.firestore_project_id:
            raise FirebaseConfigurationError("Set FIRESTORE_PROJECT_ID before using authentication.")

        try:
            if settings.firebase_credentials_path:
                credential = credentials.Certificate(str(settings.firebase_credentials_path))
                if credential.project_id != settings.firestore_project_id:
                    raise FirebaseConfigurationError(
                        "The service account and FIRESTORE_PROJECT_ID must belong to the same project."
                    )
            else:
                credential = credentials.ApplicationDefault()

            # Resolve credentials here so configuration failures stay separate from bad user tokens.
            credential.get_credential()
            return initialize_app(
                credential,
                options={"projectId": settings.firestore_project_id, "httpTimeout": 10},
            )
        except (OSError, ValueError, GoogleAuthError) as exc:
            raise FirebaseConfigurationError(
                "Firebase credentials could not be loaded. Check the credential path or ADC configuration."
            ) from exc


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_firebase_id_token(id_token: str) -> AuthenticatedUser:
    """Verify a Firebase ID token and return a role-aware application user."""

    if not isinstance(id_token, str) or not id_token.strip():
        raise _unauthorized("An authentication token is required.")

    try:
        app = get_firebase_app()
    except FirebaseConfigurationError as exc:
        logger.error("Firebase authentication configuration is unavailable.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is unavailable. Contact the administrator.",
        ) from exc

    try:
        # Revocation checks also reject disabled accounts and deleted users.
        decoded_token = auth.verify_id_token(id_token, app=app, check_revoked=True)
    except auth.ExpiredIdTokenError as exc:
        raise _unauthorized("Your session has expired. Please log in again.") from exc
    except auth.RevokedIdTokenError as exc:
        raise _unauthorized("This session has been revoked. Please log in again.") from exc
    except auth.UserDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled. Contact support.",
        ) from exc
    except auth.UserNotFoundError as exc:
        raise _unauthorized("This account is no longer available.") from exc
    except (auth.InvalidIdTokenError, ValueError) as exc:
        raise _unauthorized("Invalid authentication token.") from exc
    except (exceptions.FirebaseError, GoogleAuthError) as exc:
        # SDK exception messages may contain request data; log only the exception type.
        logger.error("Firebase verification unavailable (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable. Please try again later.",
        ) from exc

    try:
        return AuthenticatedUser.from_firebase_claims(decoded_token)
    except UnsupportedUserRoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has an unsupported role. Contact the administrator.",
        ) from exc
    except ValueError as exc:
        raise _unauthorized("Invalid authentication identity.") from exc
