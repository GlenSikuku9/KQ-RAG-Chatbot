import argparse
import logging
from pathlib import Path
import sys

from firebase_admin import auth, exceptions
from google.auth.exceptions import GoogleAuthError


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.models.user import UserRole
from app.services.firebase_auth import FirebaseConfigurationError, get_firebase_app


logger = logging.getLogger(__name__)


class RoleAssignmentError(RuntimeError):
    """A role operation requires operator intervention."""


def set_user_role(uid: str, role: UserRole, project_id: str) -> None:
    """Assign a trusted custom claim using backend credentials, never a public API."""

    app = get_firebase_app()
    if app.project_id != project_id:
        raise RoleAssignmentError("Project confirmation does not match the configured Firebase project.")

    user = auth.get_user(uid, app=app)
    claims = dict(user.custom_claims or {})
    claims["role"] = role.value
    # set_custom_user_claims replaces the entire map, so preserve unrelated claims.
    auth.set_custom_user_claims(uid, claims, app=app)

    try:
        # Old administrator tokens must not remain valid after a demotion.
        auth.revoke_refresh_tokens(uid, app=app)
    except (exceptions.FirebaseError, GoogleAuthError) as exc:
        logger.error("Session revocation failed after role update (%s).", type(exc).__name__)
        raise RoleAssignmentError(
            "The role was saved, but session revocation failed. Retry this command "
            "before considering the role change complete."
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assign a Firebase user role and revoke existing sessions."
    )
    parser.add_argument("--uid", required=True, help="Existing user's Firebase Authentication UID.")
    parser.add_argument("--role", required=True, choices=[role.value for role in UserRole])
    parser.add_argument("--project-id", required=True, help="Confirm the intended Firebase project.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        set_user_role(args.uid, UserRole(args.role), args.project_id)
    except (FirebaseConfigurationError, RoleAssignmentError) as exc:
        # These application errors contain only fixed, non-secret diagnostic messages.
        logger.error("%s", exc)
        return 1
    except (exceptions.FirebaseError, GoogleAuthError, ValueError) as exc:
        logger.error("Role assignment failed (%s). Check the UID and service-account access.", type(exc).__name__)
        return 1

    print("Role saved and existing sessions revoked. The user must sign out and sign in again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
