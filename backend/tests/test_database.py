from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote

from fastapi import HTTPException
from google.api_core.exceptions import Aborted, DeadlineExceeded, PermissionDenied, RetryError
from google.auth.exceptions import RefreshError
from google.cloud.firestore_v1 import Transaction
from google.cloud.firestore_v1._helpers import encode_value
from pydantic import TypeAdapter, ValidationError

from app.models.database import UTCDateTime, encode_document_id, utc_now
from app.models.user import UserRole
from app.models.user_profile import UserProfile
from app.services.firestore_client import (
    TRANSACTION_MAX_ATTEMPTS,
    firestore_operation,
    get_firestore_client,
    run_transaction,
)


class RecordConventionTests(unittest.TestCase):
    def test_logical_ids_round_trip_without_collisions(self):
        values = [
            "user-1", "a/b", "a%2Fb", ".", "..", "%2E", "__reserved__",
            "%5F%5Freserved%5F%5F", "with space", "msafiri-\u00e9",
        ]
        encoded = [encode_document_id(value) for value in values]
        self.assertEqual(len(set(encoded)), len(values))
        for original, document_id in zip(values, encoded, strict=True):
            self.assertEqual(unquote(document_id), original)
            self.assertNotIn("/", document_id)
            self.assertNotIn(document_id, (".", ".."))
            self.assertFalse(document_id.startswith("__") and document_id.endswith("__"))
        self.assertEqual(encode_document_id("user-1"), "user-1")

    def test_encoded_byte_limit_is_checked_exactly(self):
        self.assertEqual(len(encode_document_id("x" * 1500)), 1500)
        self.assertEqual(len(encode_document_id("\u00e9" * 250)), 1500)
        for value in ("x" * 1501, "\u00e9" * 251):
            with self.subTest(length=len(value)):
                with self.assertLogs("app.models.database", level="ERROR"):
                    with self.assertRaises(ValueError):
                        encode_document_id(value)

    def test_invalid_ids_fail_with_safe_logging(self):
        for value in ("", "   ", None, 123, "\ud800"):
            with self.subTest(value=repr(value)):
                with self.assertLogs("app.models.database", level="ERROR"):
                    with self.assertRaises(ValueError):
                        encode_document_id(value)

    def test_timestamp_requires_timezone_and_normalizes_to_utc(self):
        adapter = TypeAdapter(UTCDateTime)
        local = datetime(2026, 10, 8, 13, 0, tzinfo=timezone(timedelta(hours=3)))
        result = adapter.validate_python(local)
        self.assertEqual(result, datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(result.utcoffset(), timedelta(0))
        self.assertEqual(utc_now().utcoffset(), timedelta(0))
        for naive in (datetime(2026, 10, 8), "2026-10-08T10:00:00"):
            with self.assertRaises(ValidationError):
                adapter.validate_python(naive)

    def test_profile_serialization_is_firestore_native_and_json_compatible(self):
        timestamp = datetime(2026, 10, 8, 13, tzinfo=timezone(timedelta(hours=3)))
        profile = UserProfile(
            user_id="user-1", role=UserRole.PASSENGER, email_verified=False,
            created_at=timestamp, last_login=timestamp,
        )
        payload = profile.model_dump()
        self.assertIs(profile.role, UserRole.PASSENGER)
        self.assertIs(type(payload["role"]), str)
        self.assertIsInstance(payload["created_at"], datetime)
        self.assertEqual(payload["created_at"].utcoffset(), timedelta(0))
        self.assertEqual(encode_value(payload["role"]).string_value, "passenger")
        self.assertEqual(encode_value(payload["created_at"]).timestamp_value, timestamp)
        self.assertEqual(profile.model_dump(mode="json")["created_at"], "2026-10-08T10:00:00Z")
        self.assertIsNone(payload["email"])


class SharedFirestoreTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.transaction = Mock(spec=Transaction)
        self.transaction._id = b"test-id"
        self.transaction._read_only = False
        self.transaction._max_attempts = TRANSACTION_MAX_ATTEMPTS
        self.client.transaction.return_value = self.transaction

    def test_transaction_returns_only_after_commit(self):
        operation = Mock(return_value="result")
        self.assertEqual(run_transaction(self.client, operation), "result")
        operation.assert_called_once_with(self.transaction)
        self.transaction._commit.assert_called_once()
        self.client.transaction.assert_called_once_with(max_attempts=3)

    def test_transient_conflict_retries_callback(self):
        self.transaction._commit.side_effect = [Aborted("conflict"), None]
        operation = Mock(return_value=42)
        self.assertEqual(run_transaction(self.client, operation), 42)
        self.assertEqual(operation.call_count, 2)

    def test_conflict_exhaustion_reports_503_without_sdk_details(self):
        self.transaction._commit.side_effect = Aborted("PRIVATE-DATA")
        with self.assertLogs("app.services.firestore_client", level="WARNING") as logs:
            with self.assertRaises(HTTPException) as caught:
                run_transaction(self.client, Mock())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.transaction._commit.call_count, 3)
        self.assertNotIn("PRIVATE-DATA", caught.exception.detail + "\n".join(logs.output))

    def test_failed_commit_never_returns_callback_result(self):
        self.transaction._commit.side_effect = PermissionDenied("PRIVATE-DATA")
        with self.assertLogs("app.services.firestore_client", level="ERROR"):
            with self.assertRaises(HTTPException) as caught:
                run_transaction(self.client, Mock(return_value="not-success"))
        self.assertEqual(caught.exception.status_code, 503)

    def test_programmer_error_is_not_disguised_as_unavailability(self):
        with self.assertRaisesRegex(ValueError, "invalid operation"):
            run_transaction(self.client, Mock(side_effect=ValueError("invalid operation")))
        self.transaction._commit.assert_not_called()

    def test_http_errors_preserve_feature_status(self):
        for status in (401, 403, 404, 500):
            error = HTTPException(status_code=status, detail="Feature error")
            with self.assertRaises(HTTPException) as caught:
                with firestore_operation():
                    raise error
            self.assertIs(caught.exception, error)

    def test_availability_errors_are_generic_and_safe(self):
        errors = (
            DeadlineExceeded("PRIVATE-DATA"), PermissionDenied("PRIVATE-DATA"),
            RefreshError("PRIVATE-DATA"),
            RetryError("PRIVATE-DATA", cause=DeadlineExceeded("PRIVATE-DATA")),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with self.assertLogs("app.services.firestore_client", level="ERROR") as logs:
                    with self.assertRaises(HTTPException) as caught:
                        with firestore_operation():
                            raise error
                self.assertEqual(caught.exception.status_code, 503)
                self.assertIn("Data storage", caught.exception.detail)
                self.assertNotIn("PRIVATE-DATA", caught.exception.detail + "\n".join(logs.output))

    def test_sdk_client_initialization_failure_is_sanitized(self):
        with patch("app.services.firestore_client.get_firebase_app"):
            with patch("app.services.firestore_client.firestore.client",
                       side_effect=RefreshError("PRIVATE-CREDENTIAL")):
                with self.assertLogs("app.services.firestore_client", level="ERROR"):
                    with self.assertRaises(HTTPException) as caught:
                        get_firestore_client()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("PRIVATE-CREDENTIAL", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
