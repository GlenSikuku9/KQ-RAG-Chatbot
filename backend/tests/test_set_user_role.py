import unittest
from unittest.mock import Mock, patch

from firebase_admin import exceptions

from app.models.user import UserRole
from scripts.set_user_role import RoleAssignmentError, main, set_user_role


class RoleAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock(project_id="test-project")
        patch("scripts.set_user_role.get_firebase_app", return_value=self.app).start()
        self.get_user = patch("scripts.set_user_role.auth.get_user").start()
        self.set_claims = patch("scripts.set_user_role.auth.set_custom_user_claims").start()
        self.revoke = patch("scripts.set_user_role.auth.revoke_refresh_tokens").start()
        self.get_user.return_value.custom_claims = {"existing": "retained", "role": "passenger"}
        self.addCleanup(patch.stopall)

    def test_promotion_preserves_claims_and_revokes_sessions(self):
        set_user_role("test-user", UserRole.ADMIN, "test-project")
        self.set_claims.assert_called_once_with(
            "test-user", {"existing": "retained", "role": "admin"}, app=self.app
        )
        self.revoke.assert_called_once_with("test-user", app=self.app)

    def test_demotion_also_revokes_old_admin_sessions(self):
        self.get_user.return_value.custom_claims = {"role": "admin"}
        set_user_role("test-user", UserRole.PASSENGER, "test-project")
        self.set_claims.assert_called_once_with(
            "test-user", {"role": "passenger"}, app=self.app
        )
        self.revoke.assert_called_once_with("test-user", app=self.app)

    def test_user_without_claims_can_be_assigned(self):
        self.get_user.return_value.custom_claims = None
        set_user_role("test-user", UserRole.ADMIN, "test-project")
        self.set_claims.assert_called_once_with("test-user", {"role": "admin"}, app=self.app)

    def test_wrong_project_never_mutates_a_user(self):
        with self.assertRaises(RoleAssignmentError):
            set_user_role("test-user", UserRole.ADMIN, "wrong-project")
        self.get_user.assert_not_called()
        self.set_claims.assert_not_called()
        self.revoke.assert_not_called()

    def test_claim_write_failure_is_not_success(self):
        self.set_claims.side_effect = exceptions.UnavailableError("private response")
        with self.assertRaises(exceptions.UnavailableError):
            set_user_role("test-user", UserRole.ADMIN, "test-project")
        self.revoke.assert_not_called()

    def test_revocation_failure_reports_partial_update_without_secret_details(self):
        self.revoke.side_effect = exceptions.UnavailableError("private response")
        with self.assertLogs("scripts.set_user_role", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "role was saved") as caught:
                set_user_role("test-user", UserRole.ADMIN, "test-project")
        self.assertNotIn("private response", str(caught.exception) + "\n".join(logs.output))

    def test_cli_does_not_print_sdk_validation_details(self):
        self.get_user.side_effect = ValueError("SENSITIVE-SDK-DATA")
        with patch("sys.argv", ["set_user_role.py", "--uid", "test-user",
                                "--role", "admin", "--project-id", "test-project"]):
            with self.assertLogs("scripts.set_user_role", level="ERROR") as logs:
                self.assertEqual(main(), 1)
        self.assertNotIn("SENSITIVE-SDK-DATA", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
