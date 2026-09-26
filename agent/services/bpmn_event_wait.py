"""Hub-owned, bounded timer/message catches; no execution, threads or polling loop.

The parent runtime calls these operations under its existing run ownership and
applies consumed wakeups idempotently. See docs/architecture/bpmn-event-waits.md.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import TypeVar

from agent.services.bpmn_event_wait_contracts import (
    CatchActivation,
    EarlyMessagePolicy,
    EventWaitConflict,
    EventWaitError,
    InboundMessage,
    MessageAuthorizer,
    MessageContract,
    MessageDelivery,
    MessagePayloadValidator,
    TimerSpec,
    WaitLimits,
    WaitRunBinding,
    WaitView,
    Wakeup,
    identity,
    payload_copy,
    seconds,
)
from agent.services.bpmn_event_wait_store import EventWaitStore
from agent.services.workflow_runtime._serialization import canonical_json, sha256_json
from agent.services.workflow_runtime.errors import FencingTokenError, OptimisticConcurrencyError

T = TypeVar("T")


class BpmnEventWaitService:
    """One bound run's wait lifecycle; storage/signing/admission are injected.

    A fencing number is a persistence guard, not proof of an active Hub lease.
    Production composition must supply and enforce that lease independently;
    Native worker-attempt fences or a constant are not a run ownership port.
    """

    def __init__(
        self,
        *,
        run: WaitRunBinding,
        store: EventWaitStore,
        clock: Callable[[], float],
        fencing_token: int,
        limits: WaitLimits = WaitLimits(),
        authorizer: MessageAuthorizer | None = None,
        payload_validator: MessagePayloadValidator | None = None,
        early_messages: EarlyMessagePolicy | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        if type(fencing_token) is not int or fencing_token < 1:
            raise EventWaitError("bpmn_wait_fencing_token_invalid")
        if early_messages is not None and (
            early_messages.ttl_seconds > limits.max_message_ttl or early_messages.max_buffered > limits.max_messages
        ):
            raise EventWaitError("bpmn_wait_buffer_policy_exceeds_limits")
        self.run = run
        self._store = store
        self._clock = clock
        self._fence = fencing_token
        self._limits = limits
        self._authorizer = authorizer
        self._validator = payload_validator
        self._early = early_messages
        self._guard = guard

    def arm_timer(
        self,
        target: CatchActivation,
        timer: TimerSpec,
        *,
        timeout_seconds: float,
    ) -> WaitView:
        ttl = self._ttl(timeout_seconds)
        definition = {"kind": "timer", "timer": asdict(timer), "timeout_seconds": ttl}

        def arm(state: dict, now: float) -> WaitView:
            wait, created = self._register(state, target, definition, now, ttl)
            if created:
                due = timer.due_at if timer.due_at is not None else now + timer.duration_seconds
                seconds(due, "due_at")
                if due >= wait["expires_at"]:
                    raise EventWaitError("bpmn_wait_timer_exceeds_deadline")
                wait["due_at"] = due
            _advance(state, now)
            return _view(wait)

        return self._mutate(arm)

    def subscribe_message(
        self,
        target: CatchActivation,
        contract: MessageContract,
        *,
        timeout_seconds: float,
    ) -> WaitView:
        if self._authorizer is None or self._validator is None:
            raise EventWaitError("bpmn_wait_message_admission_unavailable")
        ttl = self._ttl(timeout_seconds)
        definition = {"kind": "message", "contract": asdict(contract), "timeout_seconds": ttl}

        def subscribe(state: dict, now: float) -> WaitView:
            wait, _ = self._register(state, target, definition, now, ttl)
            _advance(state, now)
            if wait["status"] == "waiting":
                buffered = sorted(
                    (
                        message
                        for message in state["messages"].values()
                        if message["status"] == "buffered"
                        and message["target"] == asdict(target)
                        and message["contract"] == asdict(contract)
                    ),
                    key=lambda message: (message["received_at"], message["message_id"]),
                )
                if buffered:
                    _match(wait, buffered[0], now)
            return _view(wait)

        return self._mutate(subscribe)

    def deliver_message(self, message: InboundMessage) -> MessageDelivery:
        """Validate before storage; dedupe binds the whole immutable envelope."""
        message = self._admit(message)
        fingerprint = sha256_json(asdict(message))
        key = sha256_json(message.message_id)

        def deliver(state: dict, now: float) -> MessageDelivery:
            existing = state["messages"].get(key)
            if existing is not None and existing["fingerprint"] != fingerprint:
                raise EventWaitConflict("bpmn_wait_message_id_conflict")
            _advance(state, now)
            if existing is not None:
                return _delivery(existing)
            if state["closed"]:
                raise EventWaitError("bpmn_wait_run_closed")
            if message.sent_at > now:
                raise EventWaitError("bpmn_wait_message_from_future")
            if message.expires_at <= now:
                return MessageDelivery(message.message_id, "expired", message.target)
            wait = state["waits"].get(message.target.key)
            if wait is not None:
                if wait["definition"].get("contract") != asdict(message.contract):
                    raise EventWaitConflict("bpmn_wait_message_correlation_mismatch")
                if wait["status"] != "waiting":
                    raise EventWaitError("bpmn_wait_not_waiting")
            elif self._early is None:
                raise EventWaitError("bpmn_wait_early_message_denied")
            elif sum(m["status"] == "buffered" for m in state["messages"].values()) >= self._early.max_buffered:
                raise EventWaitError("bpmn_wait_buffer_capacity_exceeded")
            if len(state["messages"]) >= self._limits.max_messages:
                raise EventWaitError("bpmn_wait_message_capacity_exceeded")
            stored = {
                "message_id": message.message_id,
                "target": asdict(message.target),
                "contract": asdict(message.contract),
                "fingerprint": fingerprint,
                "payload": message.payload,
                "received_at": now,
                "expires_at": message.expires_at,
                "status": "buffered",
            }
            if wait is None:
                stored["expires_at"] = min(message.expires_at, now + self._early.ttl_seconds)
            state["messages"][key] = stored
            if wait is not None:
                _match(wait, stored, now)
            return _delivery(stored)

        return self._mutate(deliver)

    def pending(self) -> tuple[Wakeup, ...]:
        """One bounded clock tick, returning ready receipts for this run only."""

        def collect(state: dict, now: float) -> tuple[Wakeup, ...]:
            _advance(state, now)
            return tuple(
                self._wakeup(state, wait) for _, wait in sorted(state["waits"].items()) if wait["status"] == "ready"
            )

        return self._mutate(collect)

    def is_closed(self) -> bool:
        """Expose the durable cancellation tombstone without copying wait state.

        Receipt replay remains available after cancellation. A parent runtime
        must check this tombstone before applying such a replay to successors.
        """
        return self._mutate(lambda state, now: state["closed"])

    def inspect(self, target: CatchActivation) -> WaitView | None:
        def inspect(state: dict, now: float) -> WaitView | None:
            _advance(state, now)
            wait = state["waits"].get(target.key)
            return _view(wait) if wait else None

        return self._mutate(inspect)

    def consume(self, target: CatchActivation, *, wakeup_id: str) -> Wakeup | None:
        """Atomically win against cancellation/expiry; replay the same receipt.

        This is NOT a task dispatch or a lease. The Hub must persist its applied
        receipt set and reconcile after crashes before scheduling successors.
        """
        identity(wakeup_id, "wakeup_id")

        def consume(state: dict, now: float) -> Wakeup | None:
            _advance(state, now)
            wait = state["waits"].get(target.key)
            if wait is None:
                return None
            if wait["wakeup_id"] != wakeup_id:
                raise EventWaitConflict("bpmn_wait_wakeup_binding_mismatch")
            if wait["status"] == "ready":
                wait.update(status="consumed", consumed_at=now)
            return self._wakeup(state, wait) if wait["status"] == "consumed" else None

        return self._mutate(consume)

    def cancel(self, target: CatchActivation) -> WaitView | None:
        def cancel(state: dict, now: float) -> WaitView | None:
            _advance(state, now)
            wait = state["waits"].get(target.key)
            if wait is None:
                raise EventWaitError("bpmn_wait_activation_unknown")
            _cancel_wait(wait, now)
            return _view(wait)

        return self._mutate(cancel)

    def cancel_run(self) -> tuple[WaitView, ...]:
        """Persist a tombstone even for an empty run; no late registrations."""

        def cancel(state: dict, now: float) -> tuple[WaitView, ...]:
            _advance(state, now)
            state["closed"] = True
            for wait in state["waits"].values():
                _cancel_wait(wait, now)
            for message in state["messages"].values():
                if message["status"] == "buffered":
                    message["status"] = "cancelled"
            return tuple(_view(wait) for wait in state["waits"].values())

        return self._mutate(cancel)

    def _ttl(self, value: float) -> float:
        ttl = seconds(value, "timeout")
        if ttl > self._limits.max_wait_seconds:
            raise EventWaitError("bpmn_wait_timeout_exceeds_limit")
        return ttl

    def _register(
        self,
        state: dict,
        target: CatchActivation,
        definition: dict,
        now: float,
        ttl: float,
    ) -> tuple[dict, bool]:
        existing = state["waits"].get(target.key)
        if existing is not None:
            if existing["definition"] != definition:
                raise EventWaitConflict("bpmn_wait_activation_definition_conflict")
            return existing, False
        if state["closed"]:
            raise EventWaitError("bpmn_wait_run_closed")
        if len(state["waits"]) >= self._limits.max_activations:
            raise EventWaitError("bpmn_wait_activation_capacity_exceeded")
        deadline = seconds(now + ttl, "deadline")
        wait = {
            "target": asdict(target),
            "definition": definition,
            "status": "waiting",
            "created_at": now,
            "expires_at": deadline,
            "due_at": None,
            "ready_at": None,
            "consumed_at": None,
            "message_key": None,
            "wakeup_id": "bpmn-wakeup-" + sha256_json({"run": asdict(self.run), "target": asdict(target)}),
        }
        state["waits"][target.key] = wait
        return wait, True

    def _admit(self, message: InboundMessage) -> InboundMessage:
        if message.run != self.run:
            raise EventWaitConflict("bpmn_wait_message_run_binding_mismatch")
        if self._authorizer is None or self._validator is None:
            raise EventWaitError("bpmn_wait_message_admission_unavailable")
        if message.expires_at - message.sent_at > self._limits.max_message_ttl:
            raise EventWaitError("bpmn_wait_message_ttl_exceeds_limit")
        detached = replace(
            message,
            payload=payload_copy(
                message.payload,
                max_bytes=self._limits.max_payload_bytes,
            ),
        )
        before = canonical_json(asdict(detached))
        self._authorizer.authorize(detached)
        self._validator.validate(detached.contract.schema_id, detached.payload)
        if canonical_json(asdict(detached)) != before:
            raise EventWaitError("bpmn_wait_admission_mutated_message")
        return detached

    def _mutate(self, operation: Callable[[dict, float], T]) -> T:
        for _ in range(self._limits.cas_attempts):
            if self._guard is not None:
                self._guard()
            previous = self._store.load(self.run)
            if self._fence < previous.fencing_token:
                raise FencingTokenError("bpmn_wait_fencing_token_stale")
            now = max(seconds(self._clock(), "clock"), previous.state["observed_at"])
            state = copy.deepcopy(previous.state)
            result = operation(state, now)
            if state == previous.state and self._fence == previous.fencing_token:
                if self._guard is not None:
                    self._guard()
                return result
            state["observed_at"] = now
            if len(canonical_json(state).encode("utf-8")) > self._limits.max_state_bytes:
                raise EventWaitError("bpmn_wait_state_capacity_exceeded")
            if self._guard is not None:
                self._guard()
            try:
                self._store.commit(self.run, previous=previous, state=state, fencing_token=self._fence, now=now)
                return result
            except OptimisticConcurrencyError:
                continue
        raise EventWaitConflict("bpmn_wait_contention_retry_exhausted")

    def _wakeup(self, state: dict, wait: dict) -> Wakeup:
        message = state["messages"].get(wait["message_key"])
        return Wakeup(
            run=self.run,
            target=CatchActivation(**wait["target"]),
            wakeup_id=wait["wakeup_id"],
            kind=wait["definition"]["kind"],
            ready_at=wait["ready_at"],
            consumed_at=wait["consumed_at"],
            message_id=message["message_id"] if message else None,
            payload=copy.deepcopy(message["payload"]) if message else None,
        )


def _advance(state: dict, now: float) -> None:
    for message in state["messages"].values():
        if message["status"] == "buffered" and message["expires_at"] <= now:
            message["status"] = "expired"
    for wait in state["waits"].values():
        if wait["status"] not in {"waiting", "ready"}:
            continue
        if wait["expires_at"] <= now:
            wait.update(status="expired", ended_at=now)
        elif wait["definition"]["kind"] == "timer" and wait["due_at"] <= now:
            if wait["status"] == "waiting":
                wait.update(status="ready", ready_at=now)


def _match(wait: dict, message: dict, now: float) -> None:
    wait.update(status="ready", ready_at=now, message_key=sha256_json(message["message_id"]))
    message["status"] = "matched"


def _cancel_wait(wait: dict, now: float) -> None:
    if wait["status"] in {"waiting", "ready"}:
        wait.update(status="cancelled", ended_at=now)


def _view(wait: dict) -> WaitView:
    return WaitView(
        target=CatchActivation(**wait["target"]),
        kind=wait["definition"]["kind"],
        status=wait["status"],
        created_at=wait["created_at"],
        expires_at=wait["expires_at"],
        due_at=wait["due_at"],
        wakeup_id=wait["wakeup_id"],
    )


def _delivery(message: dict) -> MessageDelivery:
    return MessageDelivery(message["message_id"], message["status"], CatchActivation(**message["target"]))
