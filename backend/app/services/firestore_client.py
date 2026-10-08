from collections.abc import Callable, Generator
from contextlib import contextmanager
import logging
from typing import TypeVar

from fastapi import HTTPException, status
from firebase_admin import firestore
from google.api_core.exceptions import Aborted, GoogleAPICallError, RetryError
from google.auth.exceptions import GoogleAuthError
from google.cloud.firestore_v1 import Client, Transaction

from app.services.firebase_auth import FirebaseConfigurationError, get_firebase_app


logger = logging.getLogger(__name__)
FIRESTORE_READ_TIMEOUT = 10
TRANSACTION_MAX_ATTEMPTS = 3
Result = TypeVar("Result")


@contextmanager
def firestore_operation() -> Generator[None, None, None]:
    """Translate infrastructure failures without exposing SDK messages or user data."""

    try:
        yield
    except (GoogleAPICallError, RetryError, GoogleAuthError, FirebaseConfigurationError) as exc:
        logger.error("Firestore operation unavailable (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data storage is temporarily unavailable. Please try again later.",
        ) from exc


def get_firestore_client() -> Client:
    # The Admin SDK caches this client on the shared Firebase app.
    with firestore_operation():
        return firestore.client(app=get_firebase_app())


def run_transaction(client: Client, operation: Callable[[Transaction], Result]) -> Result:
    """Retry atomic database work; callers must authorize before invoking this helper."""

    with firestore_operation():
        transaction = client.transaction(max_attempts=TRANSACTION_MAX_ATTEMPTS)
        try:
            return firestore.transactional(operation)(transaction)
        except ValueError as exc:
            # Only SDK-wrapped exhausted conflicts are availability failures.
            if not isinstance(exc.__cause__, Aborted):
                raise
            logger.warning("Firestore transaction exhausted conflict retries.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Data update conflicted. Please retry.",
            ) from exc
