"""Hub-owned validation and versioned ingestion of coarse browser capabilities."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

from agent.services.sfu_browser_capability_port import (
    SfuBrowserCapabilityRepositoryPort,
    SfuBrowserCapabilitySnapshot,
)


_PSEUDONYM = re.compile(r"^room-bip_[A-Za-z0-9_-]{22}$")
_TOP_LEVEL = frozenset({
    "schema", "schema_version", "capability_version", "tenant_ref", "room_ref",
    "admission_epoch", "membership_epoch", "browser_instance_pseudonym", "sequence",
    "issued_at", "ttl_seconds", "pseudonym_rotation_seconds",
    "capability_bucket_combinations_max", "report_bytes_max", "authorization_effect",
    "capability_buckets",
})
_BUCKET_FIELDS = frozenset({
    "codec_bucket", "layering_bucket", "encoded_transform_bucket", "decode_bucket", "evidence_bucket",
})
_ENUMS = {
    "codec_bucket": frozenset({"unknown", "unsupported", "audio_opus", "video_vp8", "video_h264", "video_vp9", "video_av1"}),
    "layering_bucket": frozenset({"unknown", "unsupported", "simulcast", "svc"}),
    "encoded_transform_bucket": frozenset({"unknown", "unsupported", "available"}),
    "decode_bucket": frozenset({"unknown", "unsupported", "audio_realtime", "video_baseline", "video_enhanced"}),
    "evidence_bucket": frozenset({"not_observed", "static_api_presence", "static_capability_query"}),
}


@dataclass(frozen=True, slots=True)
class SfuCapabilityAdmissionScope:
    tenant_id: str
    room_id: str
    actor_id: str
    admission_epoch: int
    membership_epoch: int


class SfuCapabilityAdmissionScopePort(Protocol):
    def resolve(self, *, tenant_id: str, room_id: str, actor_id: str) -> SfuCapabilityAdmissionScope | None: ...


class SfuCapabilityReevaluationPort(Protocol):
    """Hub policy hook. Implementations may only narrow admission/layer state."""
    def reevaluate(self, snapshot: SfuBrowserCapabilitySnapshot) -> None: ...


@dataclass(frozen=True, slots=True)
class SfuBrowserCapabilityCommand:
    raw_document: bytes
    scope: SfuCapabilityAdmissionScope
    expected_version: int


class SfuBrowserCapabilityError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 400, retry_after_seconds: int | None = None) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason_code)


class SfuBrowserCapabilityIngestionService:
    REPORT_BYTES_MAX = 2048
    ROOM_CARDINALITY_MAX = 256

    def __init__(self, repository: SfuBrowserCapabilityRepositoryPort, *,
                 reevaluation: SfuCapabilityReevaluationPort | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._repository = repository
        self._reevaluation = reevaluation
        self._clock = clock
        self._rate_lock = threading.Lock()
        self._recent: dict[tuple[str, str, str], list[int]] = {}

    def ingest(self, command: SfuBrowserCapabilityCommand) -> SfuBrowserCapabilitySnapshot:
        if len(command.raw_document) > self.REPORT_BYTES_MAX:
            raise SfuBrowserCapabilityError("sfu_capability_report_bytes_exceeded", 413)
        document = _decode(command.raw_document)
        now_ms = int(self._clock() * 1000)
        _validate_document(document, command.scope, now_ms)
        pseudonym = str(document["browser_instance_pseudonym"])
        self._enforce_rate(command.scope, pseudonym, now_ms)
        normalized = _normalized_buckets(document["capability_buckets"])
        state = "unsupported" if _unsupported(normalized) else "active"
        digest = hashlib.sha256(_canonical(document)).hexdigest()
        issued_ms = _utc_ms(str(document["issued_at"]))
        candidate = SfuBrowserCapabilitySnapshot(
            command.scope.tenant_id, command.scope.room_id, pseudonym,
            command.scope.admission_epoch, command.scope.membership_epoch,
            int(document["sequence"]), 0, "coarse-v1", _capability_class(normalized),
            normalized, state, issued_ms + 300_000, digest,
        )
        result = self._repository.save(
            candidate, expected_version=command.expected_version,
            room_cardinality_max=self.ROOM_CARDINALITY_MAX, now_ms=now_ms,
        )
        if result.status in {"saved", "replayed"} and result.snapshot is not None:
            if self._reevaluation is not None:
                try:
                    self._reevaluation.reevaluate(result.snapshot)
                except Exception as exc:
                    raise SfuBrowserCapabilityError("sfu_capability_reevaluation_unavailable", 503) from exc
            return result.snapshot
        code = 429 if result.status == "capacity" else 409
        raise SfuBrowserCapabilityError(result.reason_code, code, 5 if code == 429 else None)

    def read(self, *, scope: SfuCapabilityAdmissionScope, browser_pseudonym: str) -> SfuBrowserCapabilitySnapshot:
        _validate_pseudonym(browser_pseudonym)
        return self._repository.read(
            tenant_id=scope.tenant_id, room_id=scope.room_id,
            browser_pseudonym=browser_pseudonym, admission_epoch=scope.admission_epoch,
            membership_epoch=scope.membership_epoch, now_ms=int(self._clock() * 1000),
        )

    def revoke(self, *, scope: SfuCapabilityAdmissionScope, browser_pseudonym: str,
               expected_version: int) -> SfuBrowserCapabilitySnapshot | None:
        _validate_pseudonym(browser_pseudonym)
        result = self._repository.revoke(
            tenant_id=scope.tenant_id, room_id=scope.room_id,
            browser_pseudonym=browser_pseudonym, expected_version=expected_version,
            now_ms=int(self._clock() * 1000),
        )
        if result.status in {"saved", "replayed"}:
            return result.snapshot
        raise SfuBrowserCapabilityError(result.reason_code, 409)

    def _enforce_rate(self, scope: SfuCapabilityAdmissionScope, pseudonym: str, now_ms: int) -> None:
        key = (scope.tenant_id, scope.room_id, pseudonym)
        with self._rate_lock:
            recent = [stamp for stamp in self._recent.get(key, ()) if stamp > now_ms - 60_000]
            if len(recent) >= 4:
                raise SfuBrowserCapabilityError("sfu_capability_rate_limited", 429, 15)
            recent.append(now_ms)
            self._recent[key] = recent


def _decode(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SfuBrowserCapabilityError("sfu_capability_json_invalid") from exc
    if not isinstance(value, dict) or set(value) != _TOP_LEVEL:
        raise SfuBrowserCapabilityError("sfu_capability_schema_invalid")
    return value


def _validate_document(value: Mapping[str, object], scope: SfuCapabilityAdmissionScope, now_ms: int) -> None:
    fixed = {
        "schema": "ananta.browser-media-capability-observation.v1", "schema_version": 1,
        "capability_version": "coarse-v1", "ttl_seconds": 300,
        "pseudonym_rotation_seconds": 900, "capability_bucket_combinations_max": 8,
        "report_bytes_max": 2048, "authorization_effect": "none",
    }
    if any(value.get(key) != expected for key, expected in fixed.items()):
        raise SfuBrowserCapabilityError("sfu_capability_schema_invalid")
    if value.get("tenant_ref") != scope.tenant_id or value.get("room_ref") != scope.room_id:
        raise SfuBrowserCapabilityError("sfu_capability_cross_scope", 403)
    if value.get("admission_epoch") != scope.admission_epoch or value.get("membership_epoch") != scope.membership_epoch:
        raise SfuBrowserCapabilityError("sfu_capability_epoch_stale", 409)
    _validate_pseudonym(value.get("browser_instance_pseudonym"))
    sequence = value.get("sequence")
    if type(sequence) is not int or sequence < 1 or sequence > 2_147_483_647:
        raise SfuBrowserCapabilityError("sfu_capability_sequence_invalid")
    issued_ms = _utc_ms(value.get("issued_at"))
    if issued_ms > now_ms + 5_000 or issued_ms + 300_000 <= now_ms:
        raise SfuBrowserCapabilityError("sfu_capability_report_stale", 409)
    _normalized_buckets(value.get("capability_buckets"))


def _normalized_buckets(raw: object) -> tuple[Mapping[str, str], ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise SfuBrowserCapabilityError("sfu_capability_bucket_count_invalid")
    normalized: list[Mapping[str, str]] = []
    seen: set[bytes] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != _BUCKET_FIELDS:
            raise SfuBrowserCapabilityError("sfu_capability_bucket_invalid")
        if any(type(item[key]) is not str or item[key] not in allowed for key, allowed in _ENUMS.items()):
            raise SfuBrowserCapabilityError("sfu_capability_bucket_invalid")
        clean = {key: str(item[key]) for key in sorted(_BUCKET_FIELDS)}
        encoded = _canonical(clean)
        if encoded in seen:
            raise SfuBrowserCapabilityError("sfu_capability_bucket_duplicate")
        seen.add(encoded)
        normalized.append(clean)
    return tuple(sorted(normalized, key=_canonical))


def _unsupported(buckets: tuple[Mapping[str, str], ...]) -> bool:
    return all(item["codec_bucket"] == "unsupported" or item["decode_bucket"] == "unsupported" for item in buckets)


def _capability_class(buckets: tuple[Mapping[str, str], ...]) -> str:
    if _unsupported(buckets):
        return "unsupported"
    if any("unknown" in item.values() or "not_observed" in item.values() for item in buckets):
        return "baseline"
    return "advanced"


def _validate_pseudonym(value: object) -> None:
    if not isinstance(value, str) or not _PSEUDONYM.fullmatch(value):
        raise SfuBrowserCapabilityError("sfu_capability_pseudonym_invalid")


def _utc_ms(value: object) -> int:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z"):
        raise SfuBrowserCapabilityError("sfu_capability_issued_at_invalid")
    try:
        return int(datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc).timestamp() * 1000)
    except ValueError as exc:
        raise SfuBrowserCapabilityError("sfu_capability_issued_at_invalid") from exc


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


__all__ = [
    "SfuBrowserCapabilityCommand", "SfuBrowserCapabilityError",
    "SfuBrowserCapabilityIngestionService", "SfuCapabilityAdmissionScope",
    "SfuCapabilityAdmissionScopePort", "SfuCapabilityReevaluationPort",
]
