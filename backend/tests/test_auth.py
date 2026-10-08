import inspect
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from firebase_admin import auth, exceptions
from google.auth.exceptions import RefreshError

from app.api.dependencies import get_current_user
from app.main import create_app
from app.models.user import AuthenticatedUser, UnsupportedUserRoleError, UserRole
from app.services.firebase_auth import FirebaseConfigurationError, verify_firebase_id_token


class UserClaimsTests(unittest.TestCase):
    def test_supported_identity_keys(self):
        for key in ("uid", "user_id", "sub"):
            with self.subTest(key=key):
                user = AuthenticatedUser.from_firebase_claims({key: "test-user"})
                self.assertEqual(user.uid, "test-user")
                self.assertEqual(user.role, UserRole.PASSENGER)

    def test_missing_and_invalid_identity_rejected(self):
        for uid in (None, "", "   ", 123, "x" * 129):
            with self.subTest(uid=uid):
                with self.assertRaises(ValueError):
                    AuthenticatedUser.from_firebase_claims({"uid": uid})

    def test_unknown_roles_are_not_silently_downgraded(self):
        for role in ("customer", "owner", "ADMIN", "", None, [], {}):
            with self.subTest(role=role):
                with self.assertRaises(UnsupportedUserRoleError):
                    AuthenticatedUser.from_firebase_claims({"uid": "test-user", "role": role})

    def test_only_the_role_claim_grants_admin(self):
        user = AuthenticatedUser.from_firebase_claims(
            {"uid": "test-user", "admin": True, "name": "Administrator"}
        )
        self.assertEqual(user.role, UserRole.PASSENGER)


class AuthenticationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.app_mock = Mock()
        self.get_app = patch(
            "app.services.firebase_auth.get_firebase_app", return_value=self.app_mock
        ).start()
        self.verify = patch("app.services.firebase_auth.auth.verify_id_token").start()
        self.verify.return_value = {
            "uid": "test-user",
            "email": "passenger@example.com",
            "name": "Test Passenger",
            "email_verified": True,
        }
        self.addCleanup(patch.stopall)
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer synthetic-test-token"}

    def test_password_and_google_sign_in_use_the_same_verification(self):
        for provider in ("password", "google.com"):
            with self.subTest(provider=provider):
                self.verify.return_value["firebase"] = {"sign_in_provider": provider}
                response = self.client.get("/api/v1/auth/me", headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {
                    "uid": "test-user",
                    "email": "passenger@example.com",
                    "display_name": "Test Passenger",
                    "role": "passenger",
                    "email_verified": True,
                    "disabled": False,
                })
                self.verify.assert_called_with(
                    "synthetic-test-token", app=self.app_mock, check_revoked=True
                )
                denied = self.client.get("/api/v1/auth/admin-check", headers=self.headers)
                self.assertEqual(denied.status_code, 403)

    def test_missing_or_wrong_authorization_never_calls_firebase(self):
        for header in (None, "", "Bearer", "Bearer ", "Basic test", "Token test"):
            with self.subTest(header=header):
                headers = {} if header is None else {"Authorization": header}
                response = self.client.get("/api/v1/auth/me", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.verify.assert_not_called()
        self.get_app.assert_not_called()

    def test_empty_token_rejected_before_sdk_initialization(self):
        with self.assertRaises(HTTPException) as caught:
            verify_firebase_id_token("   ")
        self.assertEqual(caught.exception.status_code, 401)
        self.get_app.assert_not_called()

    def test_passenger_cannot_inject_an_admin_role(self):
        headers = {**self.headers, "X-Role": "admin"}
        response = self.client.get("/api/v1/auth/admin-check?role=admin", headers=headers)
        self.assertEqual(response.status_code, 403)
        mutation = self.client.post(
            "/api/v1/auth/me", headers=headers, json={"role": "admin"}
        )
        self.assertEqual(mutation.status_code, 405)

    def test_admin_can_access_both_endpoints(self):
        self.verify.return_value["role"] = "admin"
        for route in ("me", "admin-check"):
            response = self.client.get(f"/api/v1/auth/{route}", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["role"], "admin")

    def test_unknown_role_is_forbidden(self):
        self.verify.return_value["role"] = "owner"
        response = self.client.get("/api/v1/auth/me", headers=self.headers)
        self.assertEqual(response.status_code, 403)

    def test_malformed_verified_identity_is_unauthorized(self):
        for claims in ({"email": "passenger@example.com"}, {"uid": "test", "email": "invalid"}):
            with self.subTest(claims=claims):
                self.verify.return_value = claims
                response = self.client.get("/api/v1/auth/me", headers=self.headers)
                self.assertEqual(response.status_code, 401)

    def test_sdk_errors_have_safe_and_explicit_responses(self):
        private_detail = "SENSITIVE-SDK-DETAIL"
        cases = (
            (auth.ExpiredIdTokenError(private_detail, None), 401, "expired"),
            (auth.RevokedIdTokenError(private_detail), 401, "revoked"),
            (auth.InvalidIdTokenError(private_detail), 401, "Invalid"),
            (ValueError(private_detail), 401, "Invalid"),
            (auth.UserDisabledError(private_detail), 403, "disabled"),
            (auth.UserNotFoundError(private_detail), 401, "no longer"),
            (auth.CertificateFetchError(private_detail, None), 503, "unavailable"),
            (exceptions.UnavailableError(private_detail), 503, "unavailable"),
            (exceptions.PermissionDeniedError(private_detail), 503, "unavailable"),
            (RefreshError(private_detail), 503, "unavailable"),
        )
        for error, expected_status, message in cases:
            with self.subTest(error=type(error).__name__):
                self.verify.side_effect = error
                if expected_status == 503:
                    with self.assertLogs("app.services.firebase_auth", level="ERROR") as logs:
                        response = self.client.get("/api/v1/auth/me", headers=self.headers)
                    self.assertNotIn(private_detail, "\n".join(logs.output))
                else:
                    response = self.client.get("/api/v1/auth/me", headers=self.headers)
                self.assertEqual(response.status_code, expected_status)
                self.assertIn(message, response.json()["detail"])
                self.assertNotIn(private_detail, response.text)
                if expected_status == 401:
                    self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_configuration_errors_do_not_expose_paths_or_keys(self):
        self.get_app.side_effect = FirebaseConfigurationError("PRIVATE-PATH-OR-KEY")
        with self.assertLogs("app.services.firebase_auth", level="ERROR") as logs:
            response = self.client.get("/api/v1/auth/me", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE-PATH-OR-KEY", response.text + "\n".join(logs.output))
        self.verify.assert_not_called()

    def test_health_and_api_schema_do_not_initialize_firebase(self):
        for path in ("/", "/health", "/api/v1/health", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.get_app.assert_not_called()

    def test_blocking_verification_uses_a_sync_dependency(self):
        self.assertFalse(inspect.iscoroutinefunction(get_current_user))


if __name__ == "__main__":
    unittest.main()
