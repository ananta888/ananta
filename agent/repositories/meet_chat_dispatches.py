"""Consume a Hub reply reservation once; persist no input or generated media."""

from sqlalchemy import Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from agent.repositories.meet_chat_reservations import chat_event_key, receipts, room_scope_key
from agent.services.meet_contract import MeetError

_metadata = MetaData()
dispatches = Table(
    "meet_chat_reply_dispatches",
    _metadata,
    Column("intent_id", String(36), primary_key=True),
    Column("task_id", String(160), nullable=False, unique=True),
    Column("lease_id", String(160), nullable=False, unique=True),
    Column("state", String(16), nullable=False),
)


class SqlChatDispatches:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def claim(self, reservation, task_id, lease_id):
        scope = reservation.scope
        expected = {
            "scope_key": room_scope_key(scope),
            "session_id": scope.session_id,
            "sender_peer_id": reservation.sender_peer_id,
            "generation": scope.generation,
            "membership_epoch": scope.membership_epoch,
            "policy_revision": scope.policy_revision,
            "task_id": scope.task_id,
            "runtime_id": scope.runtime_id,
            "lease_id": scope.lease_id,
            "reserved_tokens": reservation.max_output_tokens,
        }
        try:
            with self.engine.begin() as connection:
                receipt = (
                    connection.execute(select(receipts).where(receipts.c.intent_id == reservation.intent_id))
                    .mappings()
                    .first()
                )
                if receipt is None or any(receipt[key] != value for key, value in expected.items()):
                    raise MeetError("meet_chat_reservation_mismatch", 409)
                # Bind the event ID too; one sender cannot mutate its correlation.
                if receipt["event_key"] != chat_event_key(
                    scope.membership_epoch, reservation.sender_peer_id, reservation.message_id
                ):
                    raise MeetError("meet_chat_reservation_mismatch", 409)
                connection.execute(
                    insert(dispatches).values(
                        intent_id=reservation.intent_id, task_id=task_id, lease_id=lease_id, state="running"
                    )
                )
        except IntegrityError:
            raise MeetError("meet_chat_already_dispatched", 409) from None
        except OperationalError:
            raise MeetError("meet_chat_dispatch_unavailable", 503) from None

    def finish(self, intent_id, task_id, lease_id, state):
        if state not in {"completed", "failed"}:
            raise MeetError("meet_chat_dispatch_state_invalid")
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(dispatches)
                    .where(
                        (dispatches.c.intent_id == intent_id)
                        & (dispatches.c.task_id == task_id)
                        & (dispatches.c.lease_id == lease_id)
                        & (dispatches.c.state == "running")
                    )
                    .values(state=state)
                )
                return result.rowcount == 1
        except OperationalError:
            raise MeetError("meet_chat_dispatch_unavailable", 503) from None
