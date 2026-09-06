"""Content-free, cross-process at-most-once reply admission in the Hub store."""

import hashlib
import json
import uuid

from sqlalchemy import BigInteger, Column, Index, Integer, MetaData, String, Table, func, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from agent.services.meet_chat_admission import ChatAdmission, ChatReservation
from agent.services.meet_contract import MeetError

_metadata = MetaData()
rooms = Table(
    "meet_chat_admission_rooms",
    _metadata,
    Column("scope_key", String(64), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("last_clock_ms", BigInteger, nullable=False),
)
receipts = Table(
    "meet_chat_admission_receipts",
    _metadata,
    Column("scope_key", String(64), primary_key=True),
    Column("event_key", String(64), primary_key=True),
    Column("intent_id", String(36), nullable=False, unique=True),
    Column("session_id", String(160), nullable=False),
    Column("sender_peer_id", String(160), nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("membership_epoch", BigInteger, nullable=False),
    Column("policy_revision", BigInteger, nullable=False),
    Column("task_id", String(160), nullable=False),
    Column("runtime_id", String(160), nullable=False),
    Column("lease_id", String(160), nullable=False),
    Column("received_ms", BigInteger, nullable=False),
    Column("reserved_tokens", Integer, nullable=False),
)
Index("ix_meet_chat_room_time", receipts.c.scope_key, receipts.c.received_ms)
Index("ix_meet_chat_session", receipts.c.scope_key, receipts.c.session_id)


def _digest(parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


class SqlChatReservations:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def reserve(self, session, event, now_ms):
        scope = session.scope
        rejection = session.policy.rejection(event, scope, now_ms)
        if rejection:
            return ChatAdmission(rejection)
        room_key = _digest([scope.origin, scope.tenant_id, scope.project_id, scope.room_id])
        # A renewal generation cannot replay the same room event. Different
        # publishers cannot consume each other's message IDs either.
        event_key = _digest([event.membership_epoch, event.sender_peer_id, event.message_id])
        try:
            with self.engine.begin() as connection:
                self._lock_room(connection, room_key, now_ms)
                return self._reserve_locked(connection, room_key, event_key, session, event, now_ms)
        except (IntegrityError, OperationalError):
            # No unsafe retry after an uncertain database/commit outcome.
            raise MeetError("meet_chat_reservation_unavailable", 503) from None

    @staticmethod
    def _lock_room(connection, room_key, now_ms):
        exists = connection.execute(select(rooms.c.scope_key).where(rooms.c.scope_key == room_key)).first()
        if not exists:
            try:
                with connection.begin_nested():
                    connection.execute(insert(rooms).values(scope_key=room_key, revision=0, last_clock_ms=now_ms))
            except IntegrityError:
                pass  # Another Hub initialized the same room; serialize below.
        # Real database row locking, not an in-process mutex. The lock spans
        # deduplication, all room/sender/session budgets and the receipt insert.
        connection.execute(update(rooms).where(rooms.c.scope_key == room_key).values(revision=rooms.c.revision + 1))

    @staticmethod
    def _reserve_locked(connection, room_key, event_key, session, event, now_ms):
        scope, policy = session.scope, session.policy
        room = receipts.c.scope_key == room_key
        duplicate = connection.execute(
            select(receipts.c.intent_id).where(room & (receipts.c.event_key == event_key))
        ).first()
        if duplicate:
            # Do not re-disclose the old intent for redispatch.
            return ChatAdmission("duplicate")
        last_clock = connection.execute(select(rooms.c.last_clock_ms).where(rooms.c.scope_key == room_key)).scalar_one()
        if now_ms < last_clock:
            return ChatAdmission("clock_regressed")
        connection.execute(update(rooms).where(rooms.c.scope_key == room_key).values(last_clock_ms=now_ms))
        session_rows = room & (receipts.c.session_id == scope.session_id)
        count, tokens = connection.execute(
            select(func.count(), func.coalesce(func.sum(receipts.c.reserved_tokens), 0)).where(session_rows)
        ).one()
        if count >= policy.max_session_replies or tokens + policy.max_output_tokens > policy.session_output_tokens:
            return ChatAdmission("session_budget_exhausted")
        recent = room & (receipts.c.received_ms > now_ms - 60_000)
        room_count, latest = connection.execute(
            select(func.count(), func.max(receipts.c.received_ms)).where(recent)
        ).one()
        if latest is not None and now_ms - latest < policy.cooldown_ms:
            return ChatAdmission("cooldown")
        if room_count >= policy.max_room_per_minute:
            return ChatAdmission("room_rate_limited")
        sender_count = connection.execute(
            select(func.count()).where(recent & (receipts.c.sender_peer_id == event.sender_peer_id))
        ).scalar_one()
        if sender_count >= policy.max_sender_per_minute:
            return ChatAdmission("sender_rate_limited")
        intent_id = str(uuid.uuid4())  # Reservation identity, not a Task or SRC_/RUN_.
        connection.execute(
            insert(receipts).values(
                scope_key=room_key,
                event_key=event_key,
                intent_id=intent_id,
                session_id=scope.session_id,
                sender_peer_id=event.sender_peer_id,
                generation=scope.generation,
                membership_epoch=scope.membership_epoch,
                policy_revision=scope.policy_revision,
                task_id=scope.task_id,
                runtime_id=scope.runtime_id,
                lease_id=scope.lease_id,
                received_ms=now_ms,
                reserved_tokens=policy.max_output_tokens,
            )
        )
        return ChatAdmission(
            "reserved",
            ChatReservation(
                intent_id,
                scope,
                event.message_id,
                event.sender_peer_id,
                policy.max_reply_chars,
                policy.max_output_tokens,
            ),
        )
