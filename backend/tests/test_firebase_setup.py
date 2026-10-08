from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from google.auth.exceptions import DefaultCredentialsError

from app.config import Settings
from app.services.firebase_auth import FirebaseConfigurationError, get_firebase_app


class FirebaseSetupTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            firestore_project_id="test-project",
            firebase_credentials_path=Path("synthetic-credentials.json"),
        )
        self.get_app = patch(
            "app.services.firebase_auth.get_app", side_effect=ValueError("Not initialized")
        ).start()
        patch("app.services.firebase_auth.get_settings", return_value=self.settings).start()
        self.certificate = patch("app.services.firebase_auth.credentials.Certificate").start()
        self.certificate.return_value.project_id = "test-project"
        self.adc = patch("app.services.firebase_auth.credentials.ApplicationDefault").start()
        self.initialize = patch("app.services.firebase_auth.initialize_app").start()
        self.addCleanup(patch.stopall)

    def test_explicit_credentials_are_initialized_once_and_reused(self):
        self.assertIs(get_firebase_app(), self.initialize.return_value)
        self.initialize.assert_called_once_with(
            self.certificate.return_value,
            options={"projectId": "test-project", "httpTimeout": 10},
        )
        self.certificate.return_value.get_credential.assert_called_once()
        self.get_app.side_effect = None
        self.get_app.return_value = self.initialize.return_value
        self.assertIs(get_firebase_app(), self.initialize.return_value)
        self.initialize.assert_called_once()

    def test_concurrent_requests_share_one_initialization(self):
        def existing_app():
            if not self.initialize.called:
                raise ValueError("Not initialized")
            return self.initialize.return_value

        self.get_app.side_effect = existing_app
        with ThreadPoolExecutor(max_workers=8) as executor:
            apps = list(executor.map(lambda _: get_firebase_app(), range(16)))
        self.assertTrue(all(app is self.initialize.return_value for app in apps))
        self.initialize.assert_called_once()

    def test_missing_project_is_a_configuration_error(self):
        self.settings.firestore_project_id = None
        with self.assertRaises(FirebaseConfigurationError):
            get_firebase_app()
        self.certificate.assert_not_called()

    def test_wrong_project_is_rejected(self):
        self.certificate.return_value.project_id = "different-project"
        with self.assertRaises(FirebaseConfigurationError):
            get_firebase_app()
        self.initialize.assert_not_called()

    def test_missing_or_malformed_key_is_a_configuration_error(self):
        for error in (FileNotFoundError("private path"), ValueError("private key data")):
            with self.subTest(error=type(error).__name__):
                self.certificate.side_effect = error
                with self.assertRaises(FirebaseConfigurationError) as caught:
                    get_firebase_app()
                self.assertNotIn(str(error), str(caught.exception))
        self.initialize.assert_not_called()

    def test_adc_used_when_no_file_is_configured(self):
        self.settings.firebase_credentials_path = None
        get_firebase_app()
        self.adc.assert_called_once()
        self.adc.return_value.get_credential.assert_called_once()
        self.certificate.assert_not_called()

    def test_unavailable_adc_is_a_configuration_error(self):
        self.settings.firebase_credentials_path = None
        self.adc.return_value.get_credential.side_effect = DefaultCredentialsError("private detail")
        with self.assertRaises(FirebaseConfigurationError):
            get_firebase_app()
        self.initialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
