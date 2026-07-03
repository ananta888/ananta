"""TriggerService — COSMOS-003

Manages trigger configurations and converts external signals into neutral
TriggerEvents. No direct worker actions are taken here; the Hub maps
TriggerEvents to Blueprints/Experts according to policy.
"""
from __future__ import annotations
import hashlib, hmac, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class TriggerKind(str, Enum):
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    MANUAL = "manual"
    FILE_CHANGE = "file_change"
    PR_EVENT = "pr_event"
    PIPELINE_EVENT = "pipeline_event"

class TriggerStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"

class TriggerEventStatus(str, Enum):
    PENDING = "pending"
    MAPPED = "mapped"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"

ALWAYS_BLOCKED_TRIGGER_KINDS = frozenset()  # none blocked by default

@dataclass
class TriggerConfig:
    trigger_id: str
    name: str
    kind: TriggerKind
    status: TriggerStatus
    scope: str          # "project" | "repo" | "global"
    project_id: str | None
    blueprint_id: str   # what blueprint to map to
    rate_limit_per_hour: int
    webhook_secret: str | None   # HMAC-SHA256 secret (None=no external webhooks)
    allowed_sources: list[str]  # empty=all internal sources allowed
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class TriggerEvent:
    event_id: str
    trigger_id: str
    kind: TriggerKind
    scope: str
    project_id: str | None
    correlation_id: str
    source: str
    payload_hash: str   # SHA-256 of sanitised payload (no secrets stored)
    status: TriggerEventStatus
    blueprint_id: str | None
    created_at: float
    audit_note: str

    def is_actionable(self) -> bool:
        return self.status == TriggerEventStatus.MAPPED and self.blueprint_id is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "trigger_id": self.trigger_id,
            "kind": self.kind.value,
            "scope": self.scope,
            "status": self.status.value,
            "blueprint_id": self.blueprint_id,
            "is_actionable": self.is_actionable(),
            "created_at": self.created_at,
        }

class TriggerValidationError(ValueError):
    pass

class WebhookSignatureError(ValueError):
    pass

class TriggerService:
    """
    Manages trigger configurations and converts external signals to TriggerEvents.

    Policy:
    - Triggers create only neutral TriggerEvents (no direct worker actions)
    - Rate limiting is enforced per trigger per hour
    - External webhooks must be HMAC-SHA256 validated
    - Paused/disabled triggers reject all incoming signals
    """

    def __init__(self) -> None:
        self._triggers: dict[str, TriggerConfig] = {}
        self._events: dict[str, TriggerEvent] = {}
        self._rate_counters: dict[str, list[float]] = {}  # trigger_id → [timestamps]

    def register(self, config: TriggerConfig) -> TriggerConfig:
        if config.rate_limit_per_hour <= 0:
            raise TriggerValidationError("rate_limit_per_hour must be > 0")
        if not config.blueprint_id.strip():
            raise TriggerValidationError("blueprint_id is required")
        self._triggers[config.trigger_id] = config
        self._rate_counters.setdefault(config.trigger_id, [])
        return config

    def pause(self, trigger_id: str) -> TriggerConfig:
        t = self._get(trigger_id)
        t.status = TriggerStatus.PAUSED
        return t

    def disable(self, trigger_id: str) -> TriggerConfig:
        t = self._get(trigger_id)
        t.status = TriggerStatus.DISABLED
        return t

    def enable(self, trigger_id: str) -> TriggerConfig:
        t = self._get(trigger_id)
        t.status = TriggerStatus.ACTIVE
        return t

    def fire(self, trigger_id: str, *, source: str = "internal",
             payload: dict[str, Any] | None = None,
             correlation_id: str | None = None,
             webhook_signature: str | None = None,
             webhook_body: bytes | None = None) -> TriggerEvent:
        """
        Fire a trigger. Returns a TriggerEvent (neutral — no worker action yet).

        External webhooks (kind=WEBHOOK with webhook_secret) MUST supply
        webhook_signature and webhook_body for HMAC validation.
        """
        t = self._get(trigger_id)
        corr = correlation_id or str(uuid.uuid4())
        payload = payload or {}

        # Disabled/paused check
        if t.status == TriggerStatus.DISABLED:
            return self._rejected_event(t, source, corr, payload, "trigger_disabled")
        if t.status == TriggerStatus.PAUSED:
            return self._rejected_event(t, source, corr, payload, "trigger_paused")

        # Source check
        if t.allowed_sources and source not in t.allowed_sources:
            return self._rejected_event(t, source, corr, payload, f"source_not_allowed:{source}")

        # Webhook signature check
        if t.kind == TriggerKind.WEBHOOK and t.webhook_secret:
            if not webhook_signature or not webhook_body:
                return self._rejected_event(t, source, corr, payload, "missing_webhook_signature")
            if not self._verify_hmac(t.webhook_secret, webhook_body, webhook_signature):
                return self._rejected_event(t, source, corr, payload, "invalid_webhook_signature")

        # Rate limit check
        if self._is_rate_limited(trigger_id, t.rate_limit_per_hour):
            return self._make_event(t, source, corr, payload, TriggerEventStatus.RATE_LIMITED, None,
                                    "rate_limit_exceeded")

        # Map to blueprint
        self._record_fire(trigger_id)
        return self._make_event(t, source, corr, payload, TriggerEventStatus.MAPPED, t.blueprint_id,
                                f"mapped_to_blueprint:{t.blueprint_id}")

    def get_event(self, event_id: str) -> TriggerEvent | None:
        return self._events.get(event_id)

    def get_events_for_trigger(self, trigger_id: str) -> list[TriggerEvent]:
        return [e for e in self._events.values() if e.trigger_id == trigger_id]

    def get_pending_events(self) -> list[TriggerEvent]:
        return [e for e in self._events.values() if e.status == TriggerEventStatus.MAPPED]

    def get_config(self, trigger_id: str) -> TriggerConfig | None:
        return self._triggers.get(trigger_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get(self, trigger_id: str) -> TriggerConfig:
        t = self._triggers.get(trigger_id)
        if t is None:
            raise KeyError(f"Unknown trigger: {trigger_id}")
        return t

    def _hash_payload(self, payload: dict) -> str:
        try:
            safe = {k: str(v) for k, v in payload.items() if k not in ("secret", "token", "key", "password")}
            raw = "|".join(f"{k}={v}" for k, v in sorted(safe.items()))
            return hashlib.sha256(raw.encode()).hexdigest()[:16]
        except Exception:
            return "unhashable"

    def _verify_hmac(self, secret: str, body: bytes, signature: str) -> bool:
        try:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception:
            return False

    def _is_rate_limited(self, trigger_id: str, limit: int) -> bool:
        now = time.time()
        cutoff = now - 3600.0
        timestamps = [ts for ts in self._rate_counters.get(trigger_id, []) if ts >= cutoff]
        self._rate_counters[trigger_id] = timestamps
        return len(timestamps) >= limit

    def _record_fire(self, trigger_id: str) -> None:
        self._rate_counters.setdefault(trigger_id, []).append(time.time())

    def _make_event(self, t: TriggerConfig, source: str, corr: str, payload: dict,
                    status: TriggerEventStatus, blueprint_id: str | None, note: str) -> TriggerEvent:
        ev = TriggerEvent(
            event_id=str(uuid.uuid4()),
            trigger_id=t.trigger_id,
            kind=t.kind,
            scope=t.scope,
            project_id=t.project_id,
            correlation_id=corr,
            source=source,
            payload_hash=self._hash_payload(payload),
            status=status,
            blueprint_id=blueprint_id,
            created_at=time.time(),
            audit_note=note,
        )
        self._events[ev.event_id] = ev
        return ev

    def _rejected_event(self, t: TriggerConfig, source: str, corr: str,
                        payload: dict, reason: str) -> TriggerEvent:
        return self._make_event(t, source, corr, payload, TriggerEventStatus.REJECTED, None, reason)


def make_trigger(*, name: str, kind: TriggerKind = TriggerKind.MANUAL,
                 blueprint_id: str = "default", scope: str = "project",
                 project_id: str | None = None, rate_limit_per_hour: int = 60,
                 webhook_secret: str | None = None,
                 allowed_sources: list[str] | None = None) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=str(uuid.uuid4()), name=name, kind=kind,
        status=TriggerStatus.ACTIVE, scope=scope, project_id=project_id,
        blueprint_id=blueprint_id, rate_limit_per_hour=rate_limit_per_hour,
        webhook_secret=webhook_secret, allowed_sources=list(allowed_sources or []),
    )
