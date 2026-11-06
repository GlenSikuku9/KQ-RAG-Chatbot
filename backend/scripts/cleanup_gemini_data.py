"""Dry-run and explicitly confirmed cleanup of retired Gemini usage data."""

import argparse
from collections import defaultdict
import json

from app.models.database import encode_document_id, utc_now
from app.services.firestore_client import (
    FIRESTORE_READ_TIMEOUT,
    firestore_operation,
    get_firestore_client,
)


CONFIRMATION = "DELETE_GEMINI_DATA"


def is_gemini_record(data: dict) -> bool:
    """Match historical Gemini aliases without matching Google's Gemma IDs."""
    identifiers = " ".join(
        str(data.get(field) or "").lower()
        for field in (
            "model_id",
            "model_provider",
            "model_version",
            "provider",
            "upstream_model",
        )
    )
    return "gemini" in identifiers or "google_ai_studio" in identifiers


def snapshots(client, collection: str) -> list:
    return list(
        client.collection(collection).stream(
            timeout=FIRESTORE_READ_TIMEOUT,
        ),
    )


def scan(client) -> tuple[dict, dict]:
    """Collect a read-only cleanup plan and non-sensitive aggregate summary."""
    collections = {
        name: snapshots(client, name)
        for name in (
            "conversations",
            "messages",
            "message_chunks",
            "feedback",
            "support_requests",
            "support_messages",
            "support_feedback",
            "operational_events",
            "model_evaluations",
        )
    }
    messages_by_conversation: dict[str, list] = defaultdict(list)
    for item in collections["messages"]:
        data = item.to_dict() or {}
        messages_by_conversation[str(data.get("conversation_id") or "")].append(
            item,
        )

    exclusive: set[str] = set()
    gemini_messages: list = []
    for conversation_id, messages in messages_by_conversation.items():
        assistant = [
            item
            for item in messages
            if (item.to_dict() or {}).get("sender_type") == "chatbot"
        ]
        matching = [
            item
            for item in assistant
            if is_gemini_record(item.to_dict() or {})
        ]
        if matching and len(matching) == len(assistant):
            exclusive.add(conversation_id)
        gemini_messages.extend(matching)

    mixed_messages = [
        item
        for item in gemini_messages
        if str((item.to_dict() or {}).get("conversation_id") or "")
        not in exclusive
    ]
    gemini_message_ids = {
        str((item.to_dict() or {}).get("message_id") or item.id)
        for item in gemini_messages
    }
    exclusive_chunks = [
        item
        for item in collections["message_chunks"]
        if str((item.to_dict() or {}).get("conversation_id") or "")
        in exclusive
    ]
    mixed_chunks = [
        item
        for item in collections["message_chunks"]
        if str((item.to_dict() or {}).get("message_id") or "")
        in gemini_message_ids
        and str((item.to_dict() or {}).get("conversation_id") or "")
        not in exclusive
    ]
    linked_support = [
        item
        for item in collections["support_requests"]
        if str((item.to_dict() or {}).get("conversation_id") or "")
        in exclusive
    ]
    active_support = [
        item
        for item in linked_support
        if (item.to_dict() or {}).get("status")
        in {"pending", "in_progress"}
    ]
    plan = {
        "collections": collections,
        "exclusive_conversation_ids": exclusive,
        "gemini_messages": gemini_messages,
        "mixed_messages": mixed_messages,
        "exclusive_chunks": exclusive_chunks,
        "mixed_chunks": mixed_chunks,
        "linked_support": linked_support,
        "active_support": active_support,
        "events": [
            item
            for item in collections["operational_events"]
            if is_gemini_record(item.to_dict() or {})
        ],
        "evaluations": [
            item
            for item in collections["model_evaluations"]
            if is_gemini_record(item.to_dict() or {})
        ],
    }
    summary = {
        "exclusive_gemini_conversations": len(exclusive),
        "mixed_model_conversations": len({
            str((item.to_dict() or {}).get("conversation_id") or "")
            for item in mixed_messages
        }),
        "gemini_messages": len(gemini_messages),
        "source_reference_records": len(exclusive_chunks) + len(mixed_chunks),
        "operational_statistics": len(plan["events"]),
        "model_evaluations": len(plan["evaluations"]),
        "linked_support_cases": len(linked_support),
        "blocked_active_support_cases": len(active_support),
    }
    return plan, summary


def delete_in_batches(client, items: list) -> None:
    for offset in range(0, len(items), 400):
        batch = client.batch()
        for item in items[offset:offset + 400]:
            batch.delete(item.reference)
        batch.commit()


def execute(client, plan: dict, summary: dict) -> None:
    """Apply the previously scanned plan; repeated runs safely find less work."""
    if plan["active_support"]:
        raise RuntimeError(
            "Resolve or cancel linked active support cases before cleanup.",
        )
    exclusive = plan["exclusive_conversation_ids"]
    collections = plan["collections"]

    exclusive_messages = [
        item
        for item in collections["messages"]
        if str((item.to_dict() or {}).get("conversation_id") or "")
        in exclusive
    ]
    exclusive_feedback = [
        item
        for item in collections["feedback"]
        if str((item.to_dict() or {}).get("conversation_id") or "")
        in exclusive
    ]
    exclusive_conversations = [
        item
        for item in collections["conversations"]
        if str((item.to_dict() or {}).get("conversation_id") or item.id)
        in exclusive
    ]
    support_ids = {
        str((item.to_dict() or {}).get("request_id") or item.id)
        for item in plan["linked_support"]
    }
    support_messages = [
        item
        for item in collections["support_messages"]
        if str((item.to_dict() or {}).get("request_id") or "")
        in support_ids
    ]
    support_feedback = [
        item
        for item in collections["support_feedback"]
        if str((item.to_dict() or {}).get("request_id") or item.id)
        in support_ids
    ]

    for items in (
        plan["exclusive_chunks"],
        plan["mixed_chunks"],
        exclusive_messages,
        plan["mixed_messages"],
        exclusive_feedback,
        support_messages,
        support_feedback,
        plan["linked_support"],
        exclusive_conversations,
        plan["events"],
        plan["evaluations"],
    ):
        delete_in_batches(client, items)

    # Recalculate sequence metadata for retained mixed-model conversations.
    mixed_ids = {
        str((item.to_dict() or {}).get("conversation_id") or "")
        for item in plan["mixed_messages"]
    }
    deleted_ids = {item.id for item in plan["mixed_messages"]}
    for conversation_id in mixed_ids:
        retained = sorted(
            (
                item
                for item in collections["messages"]
                if str((item.to_dict() or {}).get("conversation_id") or "")
                == conversation_id
                and item.id not in deleted_ids
            ),
            key=lambda item: int((item.to_dict() or {}).get("sequence") or 0),
        )
        batch = client.batch()
        for sequence, item in enumerate(retained, 1):
            batch.set(item.reference, {"sequence": sequence}, merge=True)
        batch.set(
            client.collection("conversations").document(
                encode_document_id(conversation_id),
            ),
            {"next_sequence": len(retained) + 1},
            merge=True,
        )
        batch.commit()

    audit_id = "gemini_cleanup_" + utc_now().strftime("%Y%m%d%H%M%S")
    client.collection("admin_activity").document(audit_id).set({
        "activity_id": audit_id,
        "admin_id": "maintenance_script",
        "action": "legacy_gemini_data_deleted",
        "target_type": "model_data",
        "target_id": "gemini_3_8_flash",
        "timestamp": utc_now(),
        "status": "success",
        "details": summary,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    arguments = parser.parse_args()
    with firestore_operation():
        client = get_firestore_client()
        plan, summary = scan(client)
        print(json.dumps(summary, indent=2, sort_keys=True))
        if not arguments.execute:
            print("Dry run only. No records were changed.")
            return
        if arguments.confirm != CONFIRMATION:
            raise SystemExit(
                f"Execution requires --confirm {CONFIRMATION}",
            )
        execute(client, plan, summary)
        print("Gemini cleanup completed.")


if __name__ == "__main__":
    main()
