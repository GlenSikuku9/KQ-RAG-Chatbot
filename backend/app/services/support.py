"""Secure support-case lifecycle built on the existing conversation store."""

from datetime import timedelta
from statistics import mean
from uuid import uuid4

from fastapi import HTTPException, status
from google.cloud.firestore_v1 import Client, DocumentSnapshot, Transaction
from pydantic import ValidationError

from app.config import get_settings
from app.models.database import encode_document_id, utc_now
from app.models.conversation import (
    Conversation,
    ConversationMessage,
    ConversationStatus,
    SenderType,
)
from app.models.support import (
    AgentAvailability,
    SupportAvailability,
    SupportFeedback,
    SupportFeedbackCreate,
    SupportMessage,
    SupportMessageCreate,
    SupportRequest,
    SupportRequestCreate,
    SupportRequestDetail,
    SupportRequestPage,
    SupportRequestType,
    SupportSender,
    SupportStatus,
    SupportSummary,
)
from app.models.user import AuthenticatedUser
from app.services.conversations import ConversationStore
from app.services.firestore_client import (
    FIRESTORE_READ_TIMEOUT,
    firestore_operation,
    get_firestore_client,
    run_transaction,
)


class SupportService:
    """Validate ownership and state transitions at one service boundary."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.conversations = ConversationStore(client)

    def _request_reference(self, request_id: str):
        return self.client.collection("support_requests").document(
            encode_document_id(request_id),
        )

    def _conversation_reference(self, conversation_id: str):
        return self.client.collection("conversations").document(
            encode_document_id(conversation_id),
        )

    def _message_reference(self, message_id: str):
        return self.client.collection("support_messages").document(
            encode_document_id(message_id),
        )

    @staticmethod
    def _read_request(
        snapshot: DocumentSnapshot,
        request_id: str,
    ) -> SupportRequest:
        if not snapshot.exists:
            raise HTTPException(404, "Support request not found.")
        try:
            request = SupportRequest.model_validate(snapshot.to_dict())
            if request.request_id != request_id:
                raise ValueError("Support request identity mismatch.")
            return request
        except (ValidationError, ValueError) as error:
            raise HTTPException(500, "Stored support request is invalid.") from error

    @staticmethod
    def _assert_owner(
        request: SupportRequest,
        user: AuthenticatedUser,
    ) -> None:
        if request.passenger_id != user.uid:
            raise HTTPException(404, "Support request not found.")

    def availability(self) -> SupportAvailability:
        settings = get_settings()
        cutoff = utc_now() - timedelta(
            seconds=settings.support_agent_stale_seconds,
        )
        with firestore_operation():
            snapshots = self.client.collection("agent_availability").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        live_agents = 0
        for snapshot in snapshots:
            try:
                agent = AgentAvailability.model_validate(snapshot.to_dict())
            except ValidationError:
                continue
            if agent.available and agent.last_seen >= cutoff:
                live_agents += 1
        return SupportAvailability(
            available=live_agents > 0,
            live_agents=live_agents,
            wait_seconds=settings.support_live_wait_seconds,
            clarification_attempts=settings.support_clarification_attempts,
        )

    def set_availability(
        self,
        admin: AuthenticatedUser,
        available: bool,
    ) -> AgentAvailability:
        presence = AgentAvailability(
            agent_id=admin.uid,
            agent_name=admin.display_name or admin.email,
            available=available,
            last_seen=utc_now(),
        )
        with firestore_operation():
            self.client.collection("agent_availability").document(
                encode_document_id(admin.uid),
            ).set(presence.model_dump())
        return presence

    def agent_availability(
        self,
        admin: AuthenticatedUser,
    ) -> AgentAvailability:
        with firestore_operation():
            snapshot = self.client.collection("agent_availability").document(
                encode_document_id(admin.uid),
            ).get(timeout=FIRESTORE_READ_TIMEOUT)
        if not snapshot.exists:
            return AgentAvailability(
                agent_id=admin.uid,
                agent_name=admin.display_name or admin.email,
                available=False,
                last_seen=utc_now(),
            )
        presence = AgentAvailability.model_validate(snapshot.to_dict())
        cutoff = utc_now() - timedelta(
            seconds=get_settings().support_agent_stale_seconds,
        )
        if presence.last_seen < cutoff:
            return presence.model_copy(update={"available": False})
        return presence

    def create(
        self,
        user: AuthenticatedUser,
        payload: SupportRequestCreate,
    ) -> SupportRequest:
        request_id = uuid4().hex
        now = utc_now()
        request_type = payload.request_type
        if (
            request_type == SupportRequestType.LIVE
            and not self.availability().available
        ):
            request_type = SupportRequestType.TICKET
        support_request = SupportRequest(
            request_id=request_id,
            reference=f"KQ-{now:%Y%m%d}-{request_id[:6].upper()}",
            conversation_id=payload.conversation_id,
            passenger_id=user.uid,
            passenger_name=user.display_name,
            passenger_email=user.email,
            request_type=request_type,
            status=SupportStatus.PENDING,
            reason=payload.reason,
            issue_summary=payload.issue_summary.strip(),
            created_at=now,
        )
        conversation_reference = self._conversation_reference(
            payload.conversation_id,
        )

        def operation(transaction: Transaction) -> SupportRequest:
            conversation_snapshot = conversation_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            if conversation_snapshot.exists:
                conversation = self.conversations._read_conversation(
                    conversation_snapshot,
                    payload.conversation_id,
                )
                self.conversations._assert_owner(conversation, user)
            else:
                # A direct agent request is meaningful passenger content, so
                # it may initialize a non-empty conversation without invoking AI.
                conversation = Conversation(
                    conversation_id=payload.conversation_id,
                    user_id=user.uid,
                    title=payload.issue_summary[:120],
                    language="unknown",
                    status=ConversationStatus.ACTIVE,
                    started_at=now,
                    next_sequence=2,
                )
            if conversation.active_support_request_id:
                active_snapshot = self._request_reference(
                    conversation.active_support_request_id,
                ).get(
                    transaction=transaction,
                    timeout=FIRESTORE_READ_TIMEOUT,
                )
                if active_snapshot.exists:
                    active = self._read_request(
                        active_snapshot,
                        conversation.active_support_request_id,
                    )
                    if active.status in {
                        SupportStatus.PENDING,
                        SupportStatus.IN_PROGRESS,
                    }:
                        raise HTTPException(
                            status.HTTP_409_CONFLICT,
                            "This conversation already has an active support request.",
                        )
            conversation_payload = (
                conversation_snapshot.to_dict()
                if conversation_snapshot.exists
                else self.conversations._conversation_payload(conversation)
            )
            conversation_payload["active_support_request_id"] = request_id
            transaction.set(
                conversation_reference,
                conversation_payload,
            )
            transaction.set(
                self._request_reference(request_id),
                {
                    **support_request.model_dump(),
                    "next_message_sequence": 1,
                },
            )
            if not conversation_snapshot.exists:
                first_message = ConversationMessage(
                    message_id=uuid4().hex,
                    conversation_id=payload.conversation_id,
                    sequence=1,
                    sender_type=SenderType.CUSTOMER,
                    message_text=payload.issue_summary,
                    language="unknown",
                    timestamp=now,
                )
                transaction.set(
                    self.client.collection("messages").document(
                        encode_document_id(first_message.message_id),
                    ),
                    first_message.model_dump(exclude={"retrieved_chunks"}),
                )
            return support_request

        return run_transaction(self.client, operation)

    def list_for_passenger(
        self,
        user: AuthenticatedUser,
    ) -> list[SupportRequest]:
        requests = self._all_requests()
        return sorted(
            [item for item in requests if item.passenger_id == user.uid],
            key=lambda item: item.created_at,
            reverse=True,
        )

    def detail_for_passenger(
        self,
        user: AuthenticatedUser,
        request_id: str,
    ) -> SupportRequestDetail:
        request = self.get(request_id)
        self._assert_owner(request, user)
        return self._detail(request)

    def get(self, request_id: str) -> SupportRequest:
        with firestore_operation():
            snapshot = self._request_reference(request_id).get(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        return self._read_request(snapshot, request_id)

    def _all_requests(self) -> list[SupportRequest]:
        with firestore_operation():
            snapshots = self.client.collection("support_requests").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        requests = []
        for snapshot in snapshots:
            try:
                requests.append(
                    SupportRequest.model_validate(snapshot.to_dict()),
                )
            except ValidationError:
                continue
        return requests

    def _messages(self, request_id: str) -> list[SupportMessage]:
        with firestore_operation():
            snapshots = self.client.collection("support_messages").stream(
                timeout=FIRESTORE_READ_TIMEOUT,
            )
        messages = []
        for snapshot in snapshots:
            try:
                message = SupportMessage.model_validate(snapshot.to_dict())
            except ValidationError:
                continue
            if message.request_id == request_id:
                messages.append(message)
        return sorted(messages, key=lambda item: item.sequence)

    def _feedback(self, request_id: str) -> SupportFeedback | None:
        with firestore_operation():
            snapshot = self.client.collection("support_feedback").document(
                encode_document_id(request_id),
            ).get(timeout=FIRESTORE_READ_TIMEOUT)
        if not snapshot.exists:
            return None
        return SupportFeedback.model_validate(snapshot.to_dict())

    def _detail(self, request: SupportRequest) -> SupportRequestDetail:
        conversation = self.conversations._messages(request.conversation_id)
        return SupportRequestDetail(
            request=request,
            messages=self._messages(request.request_id),
            feedback=self._feedback(request.request_id),
            ai_messages=[
                message.model_dump(mode="json")
                for message in conversation
            ],
        )

    def admin_detail(self, request_id: str) -> SupportRequestDetail:
        return self._detail(self.get(request_id))

    def cancel(
        self,
        user: AuthenticatedUser,
        request_id: str,
    ) -> SupportRequest:
        return self._transition(
            request_id,
            user,
            {SupportStatus.PENDING},
            SupportStatus.CANCELLED,
            owner=True,
        )

    def accept(
        self,
        admin: AuthenticatedUser,
        request_id: str,
    ) -> SupportRequest:
        return self._transition(
            request_id,
            admin,
            {SupportStatus.PENDING},
            SupportStatus.IN_PROGRESS,
        )

    def resolve(
        self,
        admin: AuthenticatedUser,
        request_id: str,
    ) -> SupportRequest:
        return self._transition(
            request_id,
            admin,
            {SupportStatus.IN_PROGRESS},
            SupportStatus.RESOLVED,
        )

    def reopen(
        self,
        admin: AuthenticatedUser,
        request_id: str,
    ) -> SupportRequest:
        return self._transition(
            request_id,
            admin,
            {SupportStatus.RESOLVED},
            SupportStatus.IN_PROGRESS,
        )

    def _transition(
        self,
        request_id: str,
        actor: AuthenticatedUser,
        allowed: set[SupportStatus],
        target: SupportStatus,
        *,
        owner: bool = False,
    ) -> SupportRequest:
        request_reference = self._request_reference(request_id)

        def operation(transaction: Transaction) -> SupportRequest:
            snapshot = request_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            request = self._read_request(snapshot, request_id)
            if owner:
                self._assert_owner(request, actor)
            # Transition endpoints are idempotent so a retried request cannot
            # turn a successful action into a misleading 409 response.
            if request.status == target:
                if (
                    target == SupportStatus.IN_PROGRESS
                    and request.assigned_agent_id != actor.uid
                ):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "This request has already been assigned.",
                    )
                return request
            if request.status not in allowed:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This support request cannot make that status transition.",
                )
            now = utc_now()
            updates: dict = {"status": target}
            if target == SupportStatus.IN_PROGRESS:
                if (
                    request.status == SupportStatus.PENDING
                    and request.assigned_agent_id not in {None, actor.uid}
                ):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "This request has already been assigned.",
                    )
                updates.update(
                    assigned_agent_id=actor.uid,
                    assigned_agent_name=actor.display_name or actor.email,
                    accepted_at=request.accepted_at or now,
                    resolved_at=None,
                )
            elif target == SupportStatus.RESOLVED:
                updates["resolved_at"] = now
            elif target == SupportStatus.CANCELLED:
                updates["cancelled_at"] = now
            updated = request.model_copy(update=updates)
            conversation_reference = self._conversation_reference(
                request.conversation_id,
            )
            conversation_snapshot = conversation_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            conversation_payload = conversation_snapshot.to_dict() or {}
            # Firestore transactions require every read before the first write.
            transaction.set(
                request_reference,
                {
                    **snapshot.to_dict(),
                    **updated.model_dump(),
                },
            )
            conversation_payload["support_mode"] = (
                "human"
                if target == SupportStatus.IN_PROGRESS
                else "ai"
            )
            if target in {SupportStatus.RESOLVED, SupportStatus.CANCELLED}:
                conversation_payload["active_support_request_id"] = None
            transaction.set(
                conversation_reference,
                conversation_payload,
            )
            return updated

        return run_transaction(self.client, operation)

    def convert_to_ticket(
        self,
        actor: AuthenticatedUser,
        request_id: str,
        *,
        require_owner: bool = True,
    ) -> SupportRequest:
        reference = self._request_reference(request_id)

        def operation(transaction: Transaction) -> SupportRequest:
            snapshot = reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            request = self._read_request(snapshot, request_id)
            if require_owner:
                self._assert_owner(request, actor)
            if request.status != SupportStatus.PENDING:
                raise HTTPException(409, "Only a pending request can be converted.")
            updated = request.model_copy(
                update={"request_type": SupportRequestType.TICKET},
            )
            transaction.set(
                reference,
                {**snapshot.to_dict(), **updated.model_dump()},
            )
            return updated

        return run_transaction(self.client, operation)

    def send_message(
        self,
        actor: AuthenticatedUser,
        request_id: str,
        payload: SupportMessageCreate,
        *,
        as_agent: bool,
    ) -> SupportMessage:
        request_reference = self._request_reference(request_id)
        message_id = encode_document_id(
            f"{request_id}:{actor.uid}:{payload.client_message_id}",
        )
        message_reference = self._message_reference(message_id)

        def operation(transaction: Transaction) -> SupportMessage:
            request_snapshot = request_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            request = self._read_request(request_snapshot, request_id)
            if as_agent:
                if request.assigned_agent_id != actor.uid:
                    raise HTTPException(403, "Only the assigned agent can reply.")
            else:
                self._assert_owner(request, actor)
            if request.status != SupportStatus.IN_PROGRESS:
                raise HTTPException(409, "Replies require an active support case.")
            existing = message_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_READ_TIMEOUT,
            )
            if existing.exists:
                return SupportMessage.model_validate(existing.to_dict())
            sequence = int(
                (request_snapshot.to_dict() or {}).get(
                    "next_message_sequence",
                    1,
                ),
            )
            message = SupportMessage(
                message_id=message_id,
                request_id=request_id,
                conversation_id=request.conversation_id,
                sequence=sequence,
                sender_role=(
                    SupportSender.AGENT
                    if as_agent
                    else SupportSender.PASSENGER
                ),
                sender_id=actor.uid,
                sender_name=actor.display_name or actor.email,
                message_text=payload.message_text.strip(),
                timestamp=utc_now(),
                client_message_id=payload.client_message_id,
            )
            transaction.set(message_reference, message.model_dump())
            transaction.set(
                request_reference,
                {
                    **request_snapshot.to_dict(),
                    "next_message_sequence": sequence + 1,
                },
            )
            return message

        return run_transaction(self.client, operation)

    def save_feedback(
        self,
        user: AuthenticatedUser,
        request_id: str,
        payload: SupportFeedbackCreate,
    ) -> SupportFeedback:
        request = self.get(request_id)
        self._assert_owner(request, user)
        if request.status != SupportStatus.RESOLVED:
            raise HTTPException(409, "Feedback is available after resolution.")
        feedback = SupportFeedback(
            request_id=request_id,
            passenger_id=user.uid,
            rating=payload.rating,
            comment=payload.comment.strip() if payload.comment else None,
            updated_at=utc_now(),
        )
        with firestore_operation():
            self.client.collection("support_feedback").document(
                encode_document_id(request_id),
            ).set(feedback.model_dump())
        return feedback

    def query_admin(
        self,
        page: int,
        page_size: int,
        search: str,
        request_type: str | None,
        case_status: str | None,
        assigned_agent: str | None,
        sort_order: str,
    ) -> SupportRequestPage:
        items = self._all_requests()
        query = search.strip().lower()
        if query:
            items = [
                item
                for item in items
                if query in " ".join(
                    filter(
                        None,
                        [
                            item.reference,
                            item.issue_summary,
                            item.passenger_name,
                            item.passenger_email,
                        ],
                    ),
                ).lower()
            ]
        if request_type:
            items = [
                item
                for item in items
                if item.request_type.value == request_type
            ]
        if case_status:
            items = [
                item
                for item in items
                if item.status.value == case_status
            ]
        if assigned_agent:
            items = [
                item
                for item in items
                if assigned_agent
                in {item.assigned_agent_id, item.assigned_agent_name}
            ]
        items.sort(
            key=lambda item: item.created_at,
            reverse=sort_order != "oldest",
        )
        total = len(items)
        start = (page - 1) * page_size
        return SupportRequestPage(
            items=items[start:start + page_size],
            total=total,
            page=page,
            page_size=page_size,
            agents=sorted(
                {
                    item.assigned_agent_name
                    for item in items
                    if item.assigned_agent_name
                },
            ),
        )

    def summary(self) -> SupportSummary:
        items = self._all_requests()
        acceptance = [
            (item.accepted_at - item.created_at).total_seconds()
            for item in items
            if item.accepted_at
        ]
        resolution = [
            (item.resolved_at - item.created_at).total_seconds()
            for item in items
            if item.resolved_at
        ]
        return SupportSummary(
            pending=sum(item.status == SupportStatus.PENDING for item in items),
            active_live_chats=sum(
                item.status == SupportStatus.IN_PROGRESS
                and item.request_type == SupportRequestType.LIVE
                for item in items
            ),
            open_tickets=sum(
                item.status in {
                    SupportStatus.PENDING,
                    SupportStatus.IN_PROGRESS,
                }
                and item.request_type == SupportRequestType.TICKET
                for item in items
            ),
            resolved=sum(item.status == SupportStatus.RESOLVED for item in items),
            total=len(items),
            live=sum(item.request_type == SupportRequestType.LIVE for item in items),
            tickets=sum(item.request_type == SupportRequestType.TICKET for item in items),
            average_acceptance_seconds=(mean(acceptance) if acceptance else None),
            average_resolution_seconds=(mean(resolution) if resolution else None),
        )


def get_support_service() -> SupportService:
    return SupportService(get_firestore_client())
