from collections.abc import Generator
from contextlib import contextmanager
import logging

from fastapi import HTTPException, status
from firebase_admin import firestore
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.auth.exceptions import GoogleAuthError
from google.cloud.firestore_v1 import Client

from app.services.firebase_auth import FirebaseConfigurationError, get_firebase_app


logger = logging.getLogger(__name__)


@contextmanager
def firestore_operation() -> Generator[None, None, None]:
    """Translate infrastructure failures without exposing SDK messages or user data."""

    try:
        yield
    except (GoogleAPICallError, RetryError, GoogleAuthError, FirebaseConfigurationError) as exc:
        logger.error("Firestore operation unavailable (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile storage is temporarily unavailable. Please try again later.",
        ) from exc


def get_firestore_client() -> Client:
    # The Admin SDK caches this client on the shared Firebase app.
    with firestore_operation():
        return firestore.client(app=get_firebase_app())
