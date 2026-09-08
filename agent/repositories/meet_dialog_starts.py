"""Durable single-winner start claims; no expiry or automatic redispatch."""

from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent.models.meet_dialog_start import DialogStartClaim, start_receipt
from agent.services.meet_contract import MeetError

_metadata = MetaData()
starts = Table(
    "meet_dialog_start_receipts",
    _metadata,
    Column("scope_key", String(64), primary_key=True),
    Column("request_digest", String(64), nullable=False),
    Column("token", String(36), nullable=False),
    Column("state", String(16), nullable=False),
    Column("task_id", String(160)),
    Column("session_id", String(160)),
    CheckConstraint("state IN ('pending', 'completed', 'failed')"),
)


class SqlDialogStarts:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def claim(self, scope_key, request_digest):
        token = str(uuid4())
        try:
            try:
                with self.engine.begin() as connection:
                    connection.execute(
                        insert(starts).values(
                            scope_key=scope_key, request_digest=request_digest, token=token, state="pending"
                        )
                    )
                return DialogStartClaim(scope_key, request_digest, token, True, "pending")
            except IntegrityError:
                with self.engine.connect() as connection:
                    row = (
                        connection.execute(select(starts).where(starts.c.scope_key == scope_key).limit(1))
                        .mappings()
                        .first()
                    )
                if row is None:
                    raise MeetError("meet_dialog_start_storage_unavailable", 503)
                if row["request_digest"] != request_digest:
                    raise MeetError("meet_dialog_idempotency_conflict", 409)
                return DialogStartClaim(**row, created=False)
        except SQLAlchemyError:
            raise MeetError("meet_dialog_start_storage_unavailable", 503) from None

    def finish(self, claim, receipt=None):
        if not claim.created:
            return False
        values = {"state": "failed"}
        if receipt is not None:
            accepted = start_receipt(receipt)
            values = {"state": "completed", "task_id": accepted["task_id"], "session_id": accepted["session_id"]}
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(starts)
                    .where(
                        starts.c.scope_key == claim.scope_key,
                        starts.c.request_digest == claim.request_digest,
                        starts.c.token == claim.token,
                        starts.c.state == "pending",
                    )
                    .values(**values)
                )
                return result.rowcount == 1
        except SQLAlchemyError:
            raise MeetError("meet_dialog_start_storage_unavailable", 503) from None
