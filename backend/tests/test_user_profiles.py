from datetime import datetime, timezone
import inspect
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from google.api_core.exceptions import Aborted, DeadlineExceeded, PermissionDenied
from google.auth.exceptions import RefreshError
from google.cloud.firestore_v1 import Transaction

from app.api.dependencies import get_current_user
from app.api.routes.auth import read_profile, synchronize_profile
from app.main import create_app
from app.models.user import AuthenticatedUser, UserRole
from app.services.firebase_auth import FirebaseConfigurationError
from app.services.firestore_client import get_firestore_client
from app.services.user_profiles import get_user_profile, sync_user_profile


class ProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.user = AuthenticatedUser(
            uid="passenger-1", email="passenger@example.com", display_name="Passenger",
            auth_time=1700000000,
        )
        self.client = Mock()
        self.reference = self.client.collection.return_value.document.return_value
        self.transaction = Mock(spec=Transaction)
        self.transaction._read_only = False
        self.transaction._max_attempts = 3
        self.transaction._id = b"test-transaction"
        self.client.transaction.return_value = self.transaction
        self.stored = None
        self.pending = None

        def snapshot(**kwargs):
            result = Mock()
            result.exists = self.stored is not None
            result.to_dict.return_value = self.stored
            return result

        def queue_write(reference, payload, merge):
            self.pending = payload.copy()

        def commit():
            if self.pending is not None:
                self.stored = {**(self.stored or {}), **self.pending}

        self.reference.get.side_effect = snapshot
        self.transaction.set.side_effect = queue_write
        self.transaction._commit.side_effect = commit
        self.get_client = patch(
            "app.services.user_profiles.get_firestore_client", return_value=self.client
        ).start()
        self.addCleanup(patch.stopall)

    def test_first_sync_creates_profile_using_verified_identity(self):
        before = datetime.now(timezone.utc)
        profile = sync_user_profile(self.user)
        self.assertEqual(profile.user_id, self.user.uid)
        self.assertEqual(profile.name, "Passenger")
        self.assertEqual(profile.email, self.user.email)
        self.assertEqual(profile.role, UserRole.PASSENGER)
        self.assertGreaterEqual(profile.created_at, before)
        self.assertEqual(profile.last_login, datetime.fromtimestamp(1700000000, timezone.utc))
        self.client.collection.assert_called_with("users")
        self.client.collection.return_value.document.assert_called_with("passenger-1")
        self.transaction.set.assert_called_once()
        self.transaction._commit.assert_called_once()
        self.assertEqual(set(self.stored), {
            "user_id", "name", "email", "role", "email_verified", "created_at", "last_login",
        })
        self.assertEqual(type(self.stored["role"]), str)

    def test_repeated_sync_preserves_creation_time_and_does_not_invent_login(self):
        first = sync_user_profile(self.user)
        second = sync_user_profile(self.user)
        self.assertEqual(first, second)
        self.assertEqual(self.stored["user_id"], self.user.uid)

    def test_new_login_updates_details_without_resetting_creation_time(self):
        first = sync_user_profile(self.user)
        self.user = self.user.model_copy(update={
            "display_name": "Updated Name", "email": "updated@example.com",
            "email_verified": True, "role": UserRole.ADMIN, "auth_time": 1700001000,
        })
        second = sync_user_profile(self.user)
        self.assertEqual(second.created_at, first.created_at)
        self.assertGreater(second.last_login, first.last_login)
        self.assertEqual(second.name, "Updated Name")
        self.assertTrue(second.email_verified)
        self.assertEqual(second.role, UserRole.ADMIN)

    def test_delayed_old_session_cannot_regress_profile(self):
        first = sync_user_profile(self.user)
        self.transaction.set.reset_mock()
        old = self.user.model_copy(update={"auth_time": 1699999000, "display_name": "Old"})
        self.assertEqual(sync_user_profile(old), first)
        self.transaction.set.assert_not_called()

    def test_unrelated_fields_are_preserved(self):
        sync_user_profile(self.user)
        self.stored["future_preference"] = "value"
        sync_user_profile(self.user)
        self.assertEqual(self.stored["future_preference"], "value")

    def test_get_returns_persisted_profile_without_writing(self):
        first = sync_user_profile(self.user)
        self.transaction.set.reset_mock()
        self.assertEqual(get_user_profile(self.user), first)
        self.transaction.set.assert_not_called()

    def test_get_missing_profile_is_404(self):
        with self.assertRaises(HTTPException) as caught:
            get_user_profile(self.user)
        self.assertEqual(caught.exception.status_code, 404)
        self.transaction.set.assert_not_called()

    def test_missing_sign_in_time_fails_before_database_access(self):
        self.user.auth_time = None
        with self.assertRaises(HTTPException) as caught:
            sync_user_profile(self.user)
        self.assertEqual(caught.exception.status_code, 401)
        self.get_client.assert_not_called()

    def test_corrupt_or_mismatched_profile_is_not_overwritten(self):
        valid = sync_user_profile(self.user).model_dump()
        for stored in ({}, {**valid, "user_id": "different-user"}, {**valid, "created_at": "invalid"}):
            with self.subTest(stored=stored):
                self.stored = stored
                self.transaction.set.reset_mock()
                with self.assertLogs("app.services.user_profiles", level="ERROR"):
                    with self.assertRaises(HTTPException) as caught:
                        sync_user_profile(self.user)
                self.assertEqual(caught.exception.status_code, 500)
                self.transaction.set.assert_not_called()

    def test_uid_cannot_select_a_nested_document_path(self):
        for uid, document_id in (("a/b", "a%2Fb"), ("a%2Fb", "a%252Fb"), (".", "%2E"), ("..", "%2E%2E")):
            with self.subTest(uid=uid):
                self.stored = None
                sync_user_profile(self.user.model_copy(update={"uid": uid}))
                self.client.collection.return_value.document.assert_called_with(document_id)

    def test_database_failure_is_503_and_does_not_leak_details(self):
        for error in (PermissionDenied("PRIVATE-DATA"), DeadlineExceeded("PRIVATE-DATA"), RefreshError("PRIVATE-DATA")):
            for operation in (get_user_profile, sync_user_profile):
                with self.subTest(error=type(error).__name__, operation=operation.__name__):
                    self.reference.get.side_effect = error
                    with self.assertLogs("app.services.firestore_client", level="ERROR") as logs:
                        with self.assertRaises(HTTPException) as caught:
                            operation(self.user)
                    self.assertEqual(caught.exception.status_code, 503)
                    self.assertNotIn("PRIVATE-DATA", caught.exception.detail + "\n".join(logs.output))

    def test_commit_failure_never_returns_success(self):
        self.transaction._commit.side_effect = PermissionDenied("PRIVATE-DATA")
        with self.assertLogs("app.services.firestore_client", level="ERROR"):
            with self.assertRaises(HTTPException) as caught:
                sync_user_profile(self.user)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIsNone(self.stored)

    def test_transaction_retries_reread_concurrently_created_profile(self):
        existing_creation = datetime(2023, 1, 1, tzinfo=timezone.utc)

        def conflicting_commit():
            if self.transaction._commit.call_count == 1:
                self.stored = {**self.pending, "created_at": existing_creation}
                raise Aborted("Concurrent first login")
            self.stored = self.pending.copy()

        self.transaction._commit.side_effect = conflicting_commit
        result = sync_user_profile(self.user)
        self.assertEqual(self.transaction._commit.call_count, 2)
        self.assertEqual(result.created_at, existing_creation)
        self.assertEqual(self.stored["created_at"], existing_creation)

    def test_exhausted_conflicts_are_explicit_503(self):
        self.transaction._commit.side_effect = Aborted("PRIVATE-DATA")
        with self.assertLogs("app.services.user_profiles", level="WARNING"):
            with self.assertRaises(HTTPException) as caught:
                sync_user_profile(self.user)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.transaction._commit.call_count, 3)


class ProfileEndpointTests(unittest.TestCase):
    def setUp(self):
        self.user = AuthenticatedUser(uid="self-user", auth_time=1700000000)
        self.app = create_app()
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.dependency_overrides.clear)
        self.get_profile = patch("app.api.routes.auth.get_user_profile").start()
        self.sync_profile = patch("app.api.routes.auth.sync_user_profile").start()
        self.addCleanup(patch.stopall)
        payload = {
            "user_id": "self-user", "name": None, "email": None,
            "role": "passenger", "email_verified": False,
            "created_at": datetime.now(timezone.utc),
            "last_login": datetime.fromtimestamp(1700000000, timezone.utc),
        }
        self.get_profile.return_value = payload
        self.sync_profile.return_value = payload

    def test_sync_and_read_are_scoped_to_authenticated_user(self):
        response = self.client.post("/api/v1/auth/profile/sync?uid=other-user&role=admin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], "self-user")
        self.sync_profile.assert_called_once_with(self.user)
        response = self.client.get("/api/v1/auth/profile?uid=other-user")
        self.assertEqual(response.status_code, 200)
        self.get_profile.assert_called_once_with(self.user)

    def test_client_profile_fields_are_rejected(self):
        for field in ("uid", "user_id", "role", "name", "email", "created_at", "last_login", "password"):
            with self.subTest(field=field):
                response = self.client.post("/api/v1/auth/profile/sync", json={field: "injected"})
                self.assertEqual(response.status_code, 422)
        self.sync_profile.assert_not_called()

    def test_empty_body_or_empty_object_are_supported(self):
        self.assertEqual(self.client.post("/api/v1/auth/profile/sync").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/profile/sync", json={}).status_code, 200)

    def test_anonymous_requests_cannot_reach_storage(self):
        self.app.dependency_overrides.clear()
        self.assertEqual(self.client.get("/api/v1/auth/profile").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/profile/sync").status_code, 401)
        self.sync_profile.assert_not_called()
        self.get_profile.assert_not_called()

    def test_another_users_profile_path_is_not_exposed(self):
        response = self.client.get("/api/v1/auth/profile/other-user")
        self.assertEqual(response.status_code, 404)

    def test_firestore_io_uses_sync_routes(self):
        self.assertFalse(inspect.iscoroutinefunction(read_profile))
        self.assertFalse(inspect.iscoroutinefunction(synchronize_profile))


class ProfileConfigurationTests(unittest.TestCase):
    def test_firestore_reuses_the_firebase_app(self):
        with patch("app.services.firestore_client.get_firebase_app") as get_app:
            with patch("app.services.firestore_client.firestore.client") as client:
                self.assertIs(get_firestore_client(), client.return_value)
                client.assert_called_once_with(app=get_app.return_value)

    def test_configuration_failure_is_safe_503(self):
        with patch("app.services.firestore_client.get_firebase_app",
                   side_effect=FirebaseConfigurationError("PRIVATE-CONFIG")):
            with self.assertLogs("app.services.firestore_client", level="ERROR") as logs:
                with self.assertRaises(HTTPException) as caught:
                    get_firestore_client()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("PRIVATE-CONFIG", caught.exception.detail + "\n".join(logs.output))

    def test_auth_time_is_verified_and_kept_out_of_public_identity(self):
        user = AuthenticatedUser.from_firebase_claims({"uid": "test", "auth_time": 1700000000})
        self.assertEqual(user.auth_time, 1700000000)
        self.assertNotIn("auth_time", user.model_dump())
        for invalid in (-1, True, "1700000000", 1.5, 253402300800):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    AuthenticatedUser.from_firebase_claims({"uid": "test", "auth_time": invalid})


if __name__ == "__main__":
    unittest.main()
