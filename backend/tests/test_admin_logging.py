import unittest
from unittest.mock import Mock, patch

from app.models.admin import AdminModel
from app.models.generation import ConfiguredModel
from app.models.user import AuthenticatedUser, UserRole
from app.main import create_app
from app.services import admin as admin_service


class AdminAuditLoggingTests(unittest.TestCase):
    def test_notification_routes_are_published_under_the_api_prefix(self):
        paths = create_app().openapi()["paths"]

        self.assertIn("/api/v1/admin/notifications", paths)
        self.assertIn("get", paths["/api/v1/admin/notifications"])
        self.assertIn("/api/v1/admin/notifications/read-all", paths)
        self.assertIn(
            "/api/v1/admin/notifications/{event_id}/read",
            paths,
        )

    def test_default_model_audit_records_previous_and_new_model_details(self):
        previous = ConfiguredModel(
            model_id="openrouter_gemma_26b",
            provider="openrouter",
            upstream_model="google/gemma-4-26b-a4b-it",
            configured=True,
            is_default=True,
        )
        selected = ConfiguredModel(
            model_id="openrouter_gemma_31b",
            provider="openrouter",
            upstream_model="google/gemma-4-31b-it",
            configured=True,
            is_default=False,
        )
        generation = Mock()
        generation.configured_models.return_value = [previous, selected]
        returned = AdminModel(
            **{
                **selected.model_dump(),
                "is_default": True,
            },
        )
        administrator = AuthenticatedUser(
            uid="admin-1",
            email="admin@example.com",
            role=UserRole.ADMIN,
        )

        with (
            patch(
                "app.rag.generation.get_generation_service",
                return_value=generation,
            ),
            patch.object(
                admin_service,
                "get_firestore_client",
                return_value=Mock(),
            ),
            patch.object(
                admin_service,
                "list_admin_models",
                return_value=[returned],
            ),
            patch.object(admin_service, "audit") as audit,
        ):
            result = admin_service.set_default_model(
                administrator,
                selected.model_id,
            )

        self.assertEqual(result.model_id, selected.model_id)
        details = audit.call_args.args[4]
        self.assertEqual(details["previous_model_id"], previous.model_id)
        self.assertEqual(details["new_model_id"], selected.model_id)
        self.assertEqual(details["previous_model_name"], "Gemma 4 26B A4B")
        self.assertEqual(details["new_model_name"], "Gemma 4 31B")
        self.assertEqual(details["new_model_provider"], "openrouter")
        self.assertEqual(audit.call_args.args[1], "default_model_updated")


if __name__ == "__main__":
    unittest.main()
