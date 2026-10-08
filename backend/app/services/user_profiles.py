from datetime import datetime, timezone
import logging

from fastapi import HTTPException, status
from google.cloud.firestore_v1 import Client, DocumentReference, DocumentSnapshot, Transaction
from pydantic import ValidationError

from app.models.database import encode_document_id, utc_now
from app.models.user import AuthenticatedUser
from app.models.user_profile import UserProfile
from app.services.firestore_client import (
    FIRESTORE_READ_TIMEOUT,
    firestore_operation,
    get_firestore_client,
    run_transaction,
)


logger = logging.getLogger(__name__)


def _profile_reference(client: Client, user: AuthenticatedUser) -> DocumentReference:
    return client.collection("users").document(encode_document_id(user.uid))


def _read_profile(snapshot: DocumentSnapshot, uid: str) -> UserProfile:
    try:
        profile = UserProfile.model_validate(snapshot.to_dict())
        if profile.user_id != uid:
            raise ValueError("Profile identity mismatch.")
        return profile
    except (ValidationError, ValueError) as exc:
        logger.error("Stored user profile failed validation.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored profile is invalid. Contact the administrator.",
        ) from exc


def get_user_profile(user: AuthenticatedUser) -> UserProfile:
    with firestore_operation():
        snapshot = _profile_reference(get_firestore_client(), user).get(
            timeout=FIRESTORE_READ_TIMEOUT
        )
        if not snapshot.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Profile not found. Synchronize the signed-in user first.",
            )
        return _read_profile(snapshot, user.uid)


def _sync_profile(
    transaction: Transaction,
    reference: DocumentReference,
    user: AuthenticatedUser,
    login_time: datetime,
) -> UserProfile:
    snapshot = reference.get(transaction=transaction, timeout=FIRESTORE_READ_TIMEOUT)
    existing = _read_profile(snapshot, user.uid) if snapshot.exists else None
    # A delayed request from an older session cannot regress the recorded login.
    if existing and login_time < existing.last_login:
        return existing

    profile = UserProfile(
        user_id=user.uid,
        name=user.display_name,
        email=user.email,
        role=user.role,
        email_verified=user.email_verified,
        created_at=existing.created_at if existing else utc_now(),
        last_login=login_time,
    )
    # A transaction makes concurrent first sign-ins share one profile and creation time.
    # The stored role is a profile snapshot, never an authorization source.
    transaction.set(reference, profile.model_dump(), merge=True)
    return profile


def sync_user_profile(user: AuthenticatedUser) -> UserProfile:
    if user.auth_time is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The token has no sign-in time. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    login_time = datetime.fromtimestamp(user.auth_time, tz=timezone.utc)
    with firestore_operation():
        client = get_firestore_client()
        reference = _profile_reference(client, user)
        return run_transaction(
            client, lambda transaction: _sync_profile(transaction, reference, user, login_time)
        )
