"""Hub-owned, bounded ingestion for non-authoritative receiver quality reports."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import copy
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent.services.sfu_broadcast_contract_validator import (
    ContractDefinition,
    SfuBroadcastContractValidator,
    StructuralLimits,
    ValidationContext,
)


_CONTRACT_ID = "ananta.receiver-quality-observation.v1"
_ALLOWED_METRICS = frozenset(
    {
        "rtt_ms",
        "jitter_ms",
        "packet_loss_basis_points",
        "receive_bitrate_bps",
        "decode_time_ms_per_frame",
        "freeze_duration_ms",
        "audio_gap_duration_ms",
        "buffer_level_ms",
        "viewport_width_css_px",
        "viewport_height_css_px",
        "cpu_pressure_basis_points",
    }
)
_METRIC_MAXIMUMS = {
    "rtt_ms": 60_000,
    "jitter_ms": 10_000,
    "packet_loss_basis_points": 10_000,
    "receive_bitrate_bps": 1_000_000_000,
    "decode_time_ms_per_frame": 1_000,
    "freeze_duration_ms": 2_000,
    "audio_gap_duration_ms": 2_000,
    "buffer_level_ms": 60_000,
    "viewport_width_css_px": 8_192,
    "viewport_height_css_px": 8_192,
    "cpu_pressure_basis_points": 10_000,
}
_PRIVACY_KEY_CODES = {
    "ip": "privacy_ip_forbidden",
    "ip_address": "privacy_ip_forbidden",
    "remote_address": "privacy_ip_forbidden",
    "local_address": "privacy_ip_forbidden",
    "ice_candidate": "privacy_ip_forbidden",
    "device": "privacy_device_forbidden",
    "device_id": "privacy_device_forbidden",
    "device_label": "privacy_device_forbidden",
    "fingerprint": "privacy_fingerprint_forbidden",
    "browser_fingerprint": "privacy_fingerprint_forbidden",
    "sdp": "privacy_sdp_forbidden",
    "offer_sdp": "privacy_sdp_forbidden",
    "answer_sdp": "privacy_sdp_forbidden",
    "raw_stats": "privacy_raw_stats_forbidden",
    "rtc_stats": "privacy_raw_stats_forbidden",
    "get_stats": "privacy_raw_stats_forbidden",
    "media_payload": "privacy_media_forbidden",
    "media_payload_base64": "privacy_media_forbidden",
    "audio_payload": "privacy_media_forbidden",
    "video_payload": "privacy_media_forbidden",
    "frame": "privacy_media_forbidden",
    "transcript": "privacy_transcript_forbidden",
    "caption": "privacy_transcript_forbidden",
    "embedding": "privacy_embedding_forbidden",
    "embeddings": "privacy_embedding_forbidden",
    "vector": "privacy_embedding_forbidden",
}


class SfuReceiverQualityError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SfuReceiverQualityPolicy:
    quality_reports_per_window_max: int = 12
    quality_report_window_seconds: int = 60
    quality_report_interval_ms_min: int = 5_000
    report_bytes_max: int = 8_192
    samples_per_report_max: int = 16
    sample_window_ms_max: int = 2_000
    history_reports_max: int = 12
    history_window_ms_max: int = 30_000
    observation_age_ms_max: int = 5_000
    sequence_window: int = 32
    retention_seconds: int = 30

    def __post_init__(self) -> None:
        for value in (
            self.quality_reports_per_window_max,
            self.quality_report_window_seconds,
            self.quality_report_interval_ms_min,
            self.report_bytes_max,
            self.samples_per_report_max,
            self.sample_window_ms_max,
            self.history_reports_max,
            self.history_window_ms_max,
            self.observation_age_ms_max,
            self.sequence_window,
            self.retention_seconds,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("quality_policy_invalid")
        if self.history_reports_max > self.quality_reports_per_window_max:
            raise ValueError("quality_policy_history_invalid")

    def declared_limits(self) -> dict[str, int]:
        """Return the exact limits a conforming observation must declare."""

        return {
            "history_reports_max": self.history_reports_max,
            "samples_per_report_max": self.samples_per_report_max,
            "reports_per_minute_max": self.quality_reports_per_window_max,
            "report_bytes_max": self.report_bytes_max,
            "sample_window_ms_max": self.sample_window_ms_max,
            "history_window_ms_max": self.history_window_ms_max,
            "observation_age_ms_max": self.observation_age_ms_max,
        }


@dataclass(frozen=True, slots=True)
class SfuReceiverQualityCommand:
    raw_document: bytes
    actor_id: str
    tenant_id: str
    session_id: str
    membership_epoch: int
    subscription_ref: str


@dataclass(frozen=True, slots=True)
class SfuReceiverQualityAuthority:
    tenant_ref: str
    room_ref: str
    subscriber_ref: str
    subscription_ref: str
    publication_ref: str
    browser_instance_pseudonym: str
    membership_epoch: int
    route_epoch: int
    allowed_layer: Mapping[str, Any] | None
    active: bool = True


class SfuReceiverQualityAuthorityPort(Protocol):
    def resolve(self, command: SfuReceiverQualityCommand) -> SfuReceiverQualityAuthority | None: ...


class SfuAdmissionReadPort(Protocol):
    def read_state(
        self,
        *,
        session_id: str,
        membership_epoch: int,
        actor_id: str,
        tenant_id: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SfuReceiverQualityIngestionResult:
    status: str
    reason_code: str
    retained_report_count: int
    sequence: int
    gap_count: int = 0

    def payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": self.status,
            "reason_code": self.reason_code,
            "retained_report_count": self.retained_report_count,
            "sequence": self.sequence,
            "gap_count": self.gap_count,
            "authoritative": False,
            "authorization_effect": "none",
        }


@dataclass(slots=True)
class _ReceiverWindow:
    reports: deque[tuple[float, dict[str, Any]]] = field(default_factory=deque)
    accepted_times: deque[float] = field(default_factory=deque)
    recent_sequences: deque[int] = field(default_factory=deque)
    recent_sequence_set: set[int] = field(default_factory=set)
    highest_sequence: int | None = None
    last_accepted_at: float | None = None


class AdmissionBackedSfuReceiverQualityAuthority:
    """Read-only admission check plus explicit Hub-issued route/browser binding.

    The binding registry has no browser mutation API.  A projection/route service
    may bind or revoke it inside the Hub; admission is re-read for every report.
    Missing state always denies ingestion.
    """

    def __init__(
        self,
        admission_provider: Callable[[], SfuAdmissionReadPort],
        *,
        bindings_max: int = 4_096,
    ) -> None:
        if type(bindings_max) is not int or bindings_max < 1:
            raise ValueError("quality_authority_limit_invalid")
        self._admission_provider = admission_provider
        self._bindings_max = bindings_max
        self._bindings: dict[tuple[str, str, str], SfuReceiverQualityAuthority] = {}
        self._lock = threading.RLock()

    def bind(self, *, tenant_id: str, session_id: str, authority: SfuReceiverQualityAuthority) -> None:
        _identifier(tenant_id, "tenant_id")
        _identifier(session_id, "session_id")
        _validate_authority(authority)
        key = (tenant_id, session_id, authority.subscription_ref)
        with self._lock:
            if key not in self._bindings and len(self._bindings) >= self._bindings_max:
                raise SfuReceiverQualityError("quality_authority_capacity_exceeded", 503)
            self._bindings[key] = authority

    def revoke(self, *, tenant_id: str, session_id: str, subscription_ref: str) -> None:
        key = (tenant_id, session_id, _identifier(subscription_ref, "subscription_ref"))
        with self._lock:
            self._bindings.pop(key, None)

    def resolve(self, command: SfuReceiverQualityCommand) -> SfuReceiverQualityAuthority | None:
        key = (command.tenant_id, command.session_id, command.subscription_ref)
        with self._lock:
            authority = self._bindings.get(key)
        if authority is None or not authority.active or authority.membership_epoch != command.membership_epoch:
            return None
        try:
            state = self._admission_provider().read_state(
                session_id=command.session_id,
                membership_epoch=command.membership_epoch,
                actor_id=command.actor_id,
                tenant_id=command.tenant_id,
            )
        except Exception:
            return None
        subscriptions = state.get("subscriptions")
        if not isinstance(subscriptions, list):
            return None
        matches = [
            row
            for row in subscriptions
            if isinstance(row, Mapping)
            and row.get("subscription_id") == command.subscription_ref
            and row.get("subscriber_id") == command.actor_id
            and row.get("publication_id") == authority.publication_ref
            and row.get("status") != "revoked"
            and row.get("membership_epoch") == command.membership_epoch
        ]
        if len(matches) != 1 or state.get("room_id") != authority.room_ref:
            return None
        return authority


class SfuReceiverQualityIngestionService:
    """Validate and retain low-frequency aggregates without changing authority."""

    def __init__(
        self,
        *,
        authority: SfuReceiverQualityAuthorityPort,
        validator: SfuBroadcastContractValidator,
        policy: SfuReceiverQualityPolicy | None = None,
        clock: Callable[[], float] = time.time,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._authority = authority
        self._validator = validator
        self._policy = policy or SfuReceiverQualityPolicy()
        self._clock = clock
        self._control_observer = control_observer_or_null(control_observer)
        self._windows: dict[tuple[str, str, str, str], _ReceiverWindow] = {}
        self._lock = threading.RLock()

    @observed_control_path("qos_feedback")
    def ingest(self, command: SfuReceiverQualityCommand) -> SfuReceiverQualityIngestionResult:
        self._validate_command(command)
        raw = command.raw_document
        if len(raw) > self._policy.report_bytes_max:
            raise SfuReceiverQualityError("report_bytes_exceeded", 413)
        document = _decode_document(raw)
        privacy_failure = _privacy_failure(document)
        if privacy_failure is not None:
            raise SfuReceiverQualityError(privacy_failure)
        self._validate_declared_limits(document)
        self._validate_numeric_semantics(document)

        contract_result = self._validator.validate(
            _CONTRACT_ID,
            raw,
            ValidationContext(),
        )
        if not contract_result.valid:
            reason = {
                "contract_document_bytes_exceeded": "report_bytes_exceeded",
                "contract_json_invalid": "quality_observation_json_invalid",
                "contract_unknown_property": "quality_observation_field_forbidden",
            }.get(contract_result.reason_code, "quality_observation_schema_invalid")
            raise SfuReceiverQualityError(reason, 413 if reason == "report_bytes_exceeded" else 400)

        authority = self._authority.resolve(command)
        if authority is None or not authority.active:
            raise SfuReceiverQualityError("quality_subscription_not_current", 409)
        self._validate_binding(document, command, authority)
        now = self._clock()
        if not math.isfinite(now):
            raise SfuReceiverQualityError("quality_clock_invalid", 503)

        sequence = _required_int(document.get("sequence"), "observation_sequence_invalid")
        key = (
            command.tenant_id,
            command.session_id,
            command.subscription_ref,
            authority.browser_instance_pseudonym,
        )
        with self._lock:
            window = self._windows.setdefault(key, _ReceiverWindow())
            self._prune(window, now)
            if sequence in window.recent_sequence_set:
                return SfuReceiverQualityIngestionResult(
                    "dropped", "observation_duplicate", len(window.reports), sequence
                )
            if window.highest_sequence is not None and sequence < window.highest_sequence:
                if sequence >= window.highest_sequence - self._policy.sequence_window + 1:
                    return SfuReceiverQualityIngestionResult(
                        "dropped", "observation_reordered", len(window.reports), sequence
                    )
                raise SfuReceiverQualityError("observation_sequence_stale", 409)
            if window.highest_sequence is not None and sequence == window.highest_sequence:
                return SfuReceiverQualityIngestionResult(
                    "dropped", "observation_duplicate", len(window.reports), sequence
                )
            self._validate_times(document, now)
            if (
                window.last_accepted_at is not None
                and (now - window.last_accepted_at) * 1_000
                < self._policy.quality_report_interval_ms_min
            ):
                raise SfuReceiverQualityError("report_interval_too_short", 429)
            if len(window.accepted_times) >= self._policy.quality_reports_per_window_max:
                raise SfuReceiverQualityError("report_rate_exceeded", 429)

            gap = 0
            if window.highest_sequence is not None and sequence > window.highest_sequence + 1:
                gap = sequence - window.highest_sequence - 1
            normalized = copy.deepcopy(document)
            normalized["received_at_ms"] = int(now * 1_000)
            window.reports.append((now, normalized))
            window.accepted_times.append(now)
            window.last_accepted_at = now
            window.highest_sequence = sequence
            window.recent_sequences.append(sequence)
            window.recent_sequence_set.add(sequence)
            while len(window.recent_sequences) > self._policy.sequence_window:
                expired = window.recent_sequences.popleft()
                window.recent_sequence_set.discard(expired)
            while len(window.reports) > self._policy.history_reports_max:
                window.reports.popleft()
            return SfuReceiverQualityIngestionResult(
                "accepted",
                "observation_sequence_gap" if gap else "ok",
                len(window.reports),
                sequence,
                gap,
            )

    def read_window(self, command: SfuReceiverQualityCommand) -> tuple[Mapping[str, Any], ...]:
        self._validate_command(command)
        authority = self._authority.resolve(command)
        if authority is None or not authority.active:
            raise SfuReceiverQualityError("quality_subscription_not_current", 409)
        key = (
            command.tenant_id,
            command.session_id,
            command.subscription_ref,
            authority.browser_instance_pseudonym,
        )
        now = self._clock()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                return ()
            self._prune(window, now)
            return tuple(copy.deepcopy(report) for _, report in window.reports)

    def purge_subscription(
        self,
        *,
        tenant_id: str,
        session_id: str,
        subscriber_ref: str,
        subscription_ref: str,
    ) -> int:
        _identifier(tenant_id, "tenant_id")
        _identifier(session_id, "session_id")
        _identifier(subscriber_ref, "subscriber_ref")
        normalized_subscription = _identifier(subscription_ref, "subscription_ref")
        with self._lock:
            keys = [
                key
                for key in self._windows
                if key[0] == tenant_id and key[1] == session_id and key[2] == normalized_subscription
            ]
            removed = sum(len(self._windows[key].reports) for key in keys)
            for key in keys:
                del self._windows[key]
            return removed

    def purge_participant(self, *, tenant_id: str, session_id: str, subscriber_ref: str) -> int:
        _identifier(tenant_id, "tenant_id")
        _identifier(session_id, "session_id")
        normalized_subscriber = _identifier(subscriber_ref, "subscriber_ref")
        with self._lock:
            keys = [
                key
                for key, window in self._windows.items()
                if key[0] == tenant_id
                and key[1] == session_id
                and any(
                    report.get("subscriber_ref") == normalized_subscriber
                    for _, report in window.reports
                )
            ]
            removed = sum(len(self._windows[key].reports) for key in keys)
            for key in keys:
                del self._windows[key]
            return removed

    def _validate_command(self, command: SfuReceiverQualityCommand) -> None:
        if not isinstance(command.raw_document, bytes):
            raise SfuReceiverQualityError("quality_observation_input_invalid")
        _identifier(command.actor_id, "actor_id")
        _identifier(command.tenant_id, "tenant_id")
        _identifier(command.session_id, "session_id")
        _identifier(command.subscription_ref, "subscription_ref")
        if type(command.membership_epoch) is not int or command.membership_epoch < 1:
            raise SfuReceiverQualityError("quality_membership_epoch_invalid")

    def _validate_declared_limits(self, document: Mapping[str, Any]) -> None:
        limits = document.get("limits")
        expected = self._policy.declared_limits()
        if not isinstance(limits, Mapping) or dict(limits) != expected:
            raise SfuReceiverQualityError("limit_contract_mismatch")
        samples = document.get("samples")
        if not isinstance(samples, list):
            raise SfuReceiverQualityError("quality_observation_schema_invalid")
        if len(samples) > self._policy.samples_per_report_max:
            raise SfuReceiverQualityError("sample_count_exceeded", 413)

    def _validate_numeric_semantics(self, document: Mapping[str, Any]) -> None:
        samples = document.get("samples")
        if not isinstance(samples, list):
            raise SfuReceiverQualityError("quality_observation_schema_invalid")
        previous_sequence: int | None = None
        previous_observed: datetime | None = None
        for sample in samples:
            if not isinstance(sample, Mapping):
                raise SfuReceiverQualityError("quality_observation_schema_invalid")
            sample_sequence = _required_int(
                sample.get("sample_sequence"), "sample_sequence_invalid"
            )
            if previous_sequence is not None:
                if sample_sequence == previous_sequence:
                    raise SfuReceiverQualityError("sample_sequence_duplicate")
                if sample_sequence < previous_sequence:
                    raise SfuReceiverQualityError("sample_sequence_reordered")
            previous_sequence = sample_sequence
            observed = _utc(sample.get("observed_at"), "sample_time_invalid")
            if previous_observed is not None and observed < previous_observed:
                raise SfuReceiverQualityError("sample_time_reordered")
            previous_observed = observed
            window_ms = _required_int(sample.get("window_ms"), "sample_window_invalid")
            if window_ms > self._policy.sample_window_ms_max:
                raise SfuReceiverQualityError("sample_window_exceeded")
            metrics = sample.get("metrics")
            if not isinstance(metrics, Mapping) or not metrics or not set(metrics).issubset(_ALLOWED_METRICS):
                raise SfuReceiverQualityError("quality_metric_forbidden")
            for name, value in metrics.items():
                if type(value) is not int or not math.isfinite(value):
                    raise SfuReceiverQualityError("metric_numeric_invalid")
                if value < 0:
                    raise SfuReceiverQualityError("numeric_negative")
                if value > _METRIC_MAXIMUMS[name]:
                    raise SfuReceiverQualityError("metric_out_of_range")
            for duration in ("freeze_duration_ms", "audio_gap_duration_ms"):
                if metrics.get(duration, 0) > window_ms:
                    raise SfuReceiverQualityError("metric_exceeds_sample_window")

    @staticmethod
    def _validate_binding(
        document: Mapping[str, Any],
        command: SfuReceiverQualityCommand,
        authority: SfuReceiverQualityAuthority,
    ) -> None:
        if authority.subscription_ref != command.subscription_ref:
            raise SfuReceiverQualityError("quality_subscription_not_current", 409)
        checks = (
            ("tenant_ref", authority.tenant_ref, "cross_tenant_observation"),
            ("room_ref", authority.room_ref, "cross_room_observation"),
            ("subscriber_ref", authority.subscriber_ref, "cross_subscriber_observation"),
            ("publication_ref", authority.publication_ref, "cross_publication_observation"),
            (
                "browser_instance_pseudonym",
                authority.browser_instance_pseudonym,
                "browser_pseudonym_scope_mismatch",
            ),
        )
        for field_name, expected, reason in checks:
            if document.get(field_name) != expected:
                raise SfuReceiverQualityError(reason, 403 if field_name == "subscriber_ref" else 409)
        if command.actor_id != authority.subscriber_ref:
            raise SfuReceiverQualityError("quality_subscriber_unauthorized", 403)
        route_epoch = document.get("route_epoch")
        if type(route_epoch) is not int:
            raise SfuReceiverQualityError("route_epoch_invalid")
        if route_epoch < authority.route_epoch:
            raise SfuReceiverQualityError("stale_route_epoch", 409)
        if route_epoch > authority.route_epoch:
            raise SfuReceiverQualityError("route_epoch_mismatch", 409)
        if document.get("allowed_layer") != authority.allowed_layer:
            raise SfuReceiverQualityError("allowed_layer_echo_mismatch", 409)
        if document.get("authorization_effect") != "none" or document.get("advisory_only") is not True:
            raise SfuReceiverQualityError("authority_claim_forbidden", 403)

    def _validate_times(self, document: Mapping[str, Any], now: float) -> None:
        issued = _utc(document.get("issued_at"), "observation_time_invalid").timestamp()
        if issued > now + 1:
            raise SfuReceiverQualityError("future_observation")
        if (now - issued) * 1_000 > self._policy.observation_age_ms_max:
            raise SfuReceiverQualityError("stale_observation", 409)
        samples = document.get("samples")
        assert isinstance(samples, list)
        for sample in samples:
            assert isinstance(sample, Mapping)
            observed = _utc(sample.get("observed_at"), "sample_time_invalid").timestamp()
            if observed > now + 1:
                raise SfuReceiverQualityError("future_sample")
            if (now - observed) * 1_000 > self._policy.history_window_ms_max:
                raise SfuReceiverQualityError("stale_sample", 409)

    def _prune(self, window: _ReceiverWindow, now: float) -> None:
        retention_cutoff = now - self._policy.retention_seconds
        while window.reports and window.reports[0][0] < retention_cutoff:
            window.reports.popleft()
        rate_cutoff = now - self._policy.quality_report_window_seconds
        while window.accepted_times and window.accepted_times[0] <= rate_cutoff:
            window.accepted_times.popleft()


class _UtcClock:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)


class _UnusedTrustStore:
    def verify(self, contract_id: str, document: Mapping[str, Any]) -> bool:
        return False


def build_sfu_receiver_quality_validator(
    *, clock: Callable[[], float] = time.time
) -> SfuBroadcastContractValidator:
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "schemas/webrtc/receiver_quality_observation.v1.json"
    definitions_path = root / "schemas/webrtc/sfu_broadcast_extension_defs.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
    return SfuBroadcastContractValidator(
        definitions=[
            ContractDefinition(
                contract_id=_CONTRACT_ID,
                schema_version="1",
                schema=schema,
                signature_required=False,
            )
        ],
        clock=_UtcClock(clock),
        trust_store=_UnusedTrustStore(),
        limits=StructuralLimits(
            max_document_bytes=8_192,
            max_depth=10,
            max_nodes=512,
            max_collection_items=64,
            max_string_bytes=2_048,
            max_total_string_bytes=8_192,
        ),
        registry_resources={definitions["$id"]: definitions},
    )


def _decode_document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                SfuReceiverQualityError("invalid_json_non_finite")
            ),
        )
    except SfuReceiverQualityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise SfuReceiverQualityError("quality_observation_json_invalid") from exc
    if not isinstance(value, dict):
        raise SfuReceiverQualityError("quality_observation_root_invalid")
    return value


def _privacy_failure(document: Mapping[str, Any]) -> str | None:
    stack: list[Any] = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                reason = _PRIVACY_KEY_CODES.get(normalized)
                if reason is not None:
                    return reason
                stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)
    return None


def _validate_authority(authority: SfuReceiverQualityAuthority) -> None:
    for name in (
        "tenant_ref",
        "room_ref",
        "subscriber_ref",
        "subscription_ref",
        "publication_ref",
        "browser_instance_pseudonym",
    ):
        _identifier(getattr(authority, name), name)
    if type(authority.membership_epoch) is not int or authority.membership_epoch < 1:
        raise SfuReceiverQualityError("quality_membership_epoch_invalid")
    if type(authority.route_epoch) is not int or authority.route_epoch < 1:
        raise SfuReceiverQualityError("route_epoch_invalid")
    if authority.allowed_layer is not None and not isinstance(authority.allowed_layer, Mapping):
        raise SfuReceiverQualityError("allowed_layer_invalid")


def _identifier(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 128
        or any(character.isspace() or ord(character) < 0x21 for character in value)
    ):
        raise SfuReceiverQualityError(f"quality_{field_name}_invalid")
    return value


def _required_int(value: Any, reason_code: str) -> int:
    if type(value) is not int or value < 1 or value > 2_147_483_647:
        raise SfuReceiverQualityError(reason_code)
    return value


def _utc(value: Any, reason_code: str) -> datetime:
    if not isinstance(value, str):
        raise SfuReceiverQualityError(reason_code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SfuReceiverQualityError(reason_code) from exc
    if parsed.tzinfo is None:
        raise SfuReceiverQualityError(reason_code)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "AdmissionBackedSfuReceiverQualityAuthority",
    "SfuReceiverQualityAuthority",
    "SfuReceiverQualityAuthorityPort",
    "SfuReceiverQualityCommand",
    "SfuReceiverQualityError",
    "SfuReceiverQualityIngestionResult",
    "SfuReceiverQualityIngestionService",
    "SfuReceiverQualityPolicy",
    "build_sfu_receiver_quality_validator",
]
