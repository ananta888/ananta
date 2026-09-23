"""Optional AudioDecision specialist backed by the whisper.cpp-audio-decision fork.

The fork runs as a separate local ``whisper-server`` process. Ananta talks HTTP only
(``POST /v1/audio/decisions``, ``GET /v1/audio/decisions/profiles``); there is no
linking and no shared memory. The client rules mirror the fork's reference client
``examples/decision/client/audio_decision_client.py`` (api ``audio.decision.v1``):

* every outcome is a :class:`DecisionOutcome`; transport errors, timeouts, HTTP errors
  and malformed responses are ``ok=False`` and carry no value;
* only fields with ``status == "ok"`` carry a value; ``abstain``, ``ambiguous`` and
  ``unsupported`` never do, and ``None`` is never replaced by a default label;
* a decision value is advisory. Nothing here grants a permission; the hub owns policy,
  permissions, confirmation and fallback.

Ananta's own audio decoding and limits (``SafeAudioDecoder``/``AudioDecodeLimits``) run
before the upload; clips longer than 30 s are rejected locally and never sent. Audio,
transcripts, labels and the API key are never logged.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import math
import os
import re
import socket
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..execution_control import BackendCancellationToken
from ..preprocessing.audio_decode import AudioDecodeError, AudioDecodeLimits, AudioDecoder, SafeAudioDecoder

_log = logging.getLogger(__name__)

API_VERSION = "audio.decision.v1"
DECISIONS_PATH = "/v1/audio/decisions"
PROFILES_PATH = "/v1/audio/decisions/profiles"
MAX_DECISION_AUDIO_MS = 30_000
FIELD_STATUSES = frozenset({"ok", "abstain", "ambiguous", "unsupported"})
FALLBACK_POLICIES = frozenset({"none", "transcribe"})
DEFAULT_PROFILES = ("speech-commands-en", "home-control-en", "confirm-en-de")
# Abstract intents are at chance level when scored directly (fork capability matrix);
# these profiles always run with fallback=transcribe so the hub's System-2 gets a transcript.
DEFAULT_SEMANTIC_PROFILES = ("intent-semantic-experimental",)

_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FIELD_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_LANGUAGE = re.compile(r"^(auto|[a-z]{2,3}(-[a-z0-9]{2,8})?)$")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_SECRET_BYTES = 16 * 1024
_SECRET_ROOTS = (Path("/run/secrets"),)


class AudioDecisionConfigurationError(ValueError):
    """Invalid AudioDecision settings; raised only when the feature is enabled."""


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    return tuple(dict.fromkeys(item.strip() for item in str(value).split(",") if item.strip()))


def _read_secret_file(path_text: str, *, allowed_roots: tuple[Path, ...]) -> str:
    path = Path(path_text)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_API_KEY_FILE cannot be read") from exc
    if allowed_roots and not any(resolved.is_relative_to(root.resolve()) for root in allowed_roots):
        raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_API_KEY_FILE must live in the secret store")
    if not resolved.is_file() or resolved.stat().st_size > _MAX_SECRET_BYTES:
        raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_API_KEY_FILE is not a small regular file")
    try:
        return resolved.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_API_KEY_FILE cannot be read") from exc


def _is_local_host(host: str) -> bool:
    if host == "localhost" or "." not in host:
        # loopback name or single-label compose service name
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


@dataclass(frozen=True)
class AudioDecisionConfig:
    enabled: bool = False
    url: str = "http://127.0.0.1:8081"
    api_key: str | None = field(default=None, repr=False)
    timeout_ms: int = 3_000
    profiles: tuple[str, ...] = DEFAULT_PROFILES
    semantic_profiles: tuple[str, ...] = DEFAULT_SEMANTIC_PROFILES
    max_audio_ms: int = MAX_DECISION_AUDIO_MS

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        secret_roots: tuple[Path, ...] = _SECRET_ROOTS,
    ) -> "AudioDecisionConfig":
        env = os.environ if environ is None else environ
        if not _as_bool(env.get("VOICE_AUDIO_DECISION_ENABLED")):
            # Feature off: read nothing else, not even the secret file.
            return cls(enabled=False)
        key_file = str(env.get("VOICE_AUDIO_DECISION_API_KEY_FILE") or "").strip()
        api_key = (
            _read_secret_file(key_file, allowed_roots=secret_roots)
            if key_file
            else str(env.get("VOICE_AUDIO_DECISION_API_KEY") or "").strip()
        )
        raw_timeout = str(env.get("VOICE_AUDIO_DECISION_TIMEOUT_MS") or "3000").strip()
        try:
            timeout_ms = int(raw_timeout)
        except ValueError as exc:
            raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_TIMEOUT_MS must be an integer") from exc
        config = cls(
            enabled=True,
            url=str(env.get("VOICE_AUDIO_DECISION_URL") or cls.url).strip(),
            api_key=api_key or None,
            timeout_ms=timeout_ms,
            profiles=_csv(env.get("VOICE_AUDIO_DECISION_PROFILES"), DEFAULT_PROFILES),
            semantic_profiles=_csv(env.get("VOICE_AUDIO_DECISION_SEMANTIC_PROFILES"), DEFAULT_SEMANTIC_PROFILES),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.enabled:
            return
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_URL must be http(s)://host[:port]")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AudioDecisionConfigurationError(
                "VOICE_AUDIO_DECISION_URL must not carry credentials, query or fragment"
            )
        if parsed.scheme == "http" and not _is_local_host(parsed.hostname):
            raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_URL needs https for non-local hosts")
        if not self.api_key or len(self.api_key) < 16 or any(ch.isspace() for ch in self.api_key):
            raise AudioDecisionConfigurationError(
                "VOICE_AUDIO_DECISION_API_KEY(_FILE) with at least 16 non-space characters is required"
            )
        if not 100 <= self.timeout_ms <= 60_000:
            raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_TIMEOUT_MS must be between 100 and 60000")
        if not self.profiles:
            raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_PROFILES must name at least one profile")
        for profile in (*self.profiles, *self.semantic_profiles):
            if not _PROFILE_ID.match(profile):
                raise AudioDecisionConfigurationError("VOICE_AUDIO_DECISION_PROFILES contains an invalid profile id")
        if not 0 < self.max_audio_ms <= MAX_DECISION_AUDIO_MS:
            raise AudioDecisionConfigurationError("audio decision clips are limited to 30 s")


@dataclass(frozen=True)
class FieldOutcome:
    name: str
    status: str  # ok | abstain | ambiguous | unsupported
    value: Any  # None unless status == "ok"
    probability: float  # relative to the candidates
    coverage: float  # absolute mass of the candidate set
    calibrated: bool
    confidence: float | None  # calibrated P(correct), None if uncalibrated
    reasons: tuple[str, ...] = ()
    matched_from_transcript: str | None = None  # lexical fallback match, never a value

    @property
    def accepted(self) -> bool:
        return self.status == "ok" and self.value is not None


@dataclass(frozen=True)
class DecisionProvenance:
    model: str | None = None
    profile_id: str | None = None
    profile_version: str | None = None
    profile_status: str | None = None
    n_encode: int | None = None
    n_encode_total: int | None = None
    fallback_provenance: str | None = None
    api_version: str = API_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "profile": {"id": self.profile_id, "version": self.profile_version, "status": self.profile_status},
            "usage": {"n_encode": self.n_encode, "n_encode_total": self.n_encode_total},
            "fallback_provenance": self.fallback_provenance,
            "api_version": self.api_version,
        }


@dataclass(frozen=True)
class DecisionOutcome:
    ok: bool  # False: no decision at all (error_code tells why)
    error_code: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    fields: Mapping[str, FieldOutcome] = field(default_factory=dict)
    language: str | None = None
    no_speech_prob: float | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    transcript: str | None = field(default=None, repr=False)
    system2_required: bool = False
    total_ms: float | None = None
    provenance: DecisionProvenance = field(default_factory=DecisionProvenance)

    def value(self, name: str) -> Any:
        item = self.fields.get(name)
        return item.value if item is not None and item.accepted else None

    @classmethod
    def failure(cls, code: str, message: str | None = None, *, http_status: int | None = None) -> "DecisionOutcome":
        return cls(ok=False, error_code=code, error_message=message, http_status=http_status)


class AudioDecisionTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> tuple[int, bytes]: ...


class HttpClientTransport:
    """Stdlib HTTP transport: no redirects, no proxies, bounded response body."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> tuple[int, bytes]:
        parsed = urllib.parse.urlparse(url)
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=timeout_s)
        try:
            connection.request(method, parsed.path or "/", body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(data) > _MAX_RESPONSE_BYTES:
                raise http.client.HTTPException("audio decision response too large")
            return response.status, data
        finally:
            connection.close()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    number = _float(value, math.nan)
    return None if math.isnan(number) else number


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def parse_decision_response(status: int | None, body: bytes, *, expected_profile: str | None = None) -> DecisionOutcome:
    """Map an HTTP answer to a fail-closed :class:`DecisionOutcome` (reference-client rules)."""
    if status is None:
        return DecisionOutcome.failure("unavailable", "audio decision service not reachable")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return DecisionOutcome.failure("bad_response", "response is not JSON", http_status=status)
    if not isinstance(payload, dict):
        return DecisionOutcome.failure("bad_response", "response is not a JSON object", http_status=status)
    if status != 200 or payload.get("object") != "audio.decision":
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = _optional_str(error.get("code")) or f"http_{status}"
        return DecisionOutcome.failure(code, _optional_str(error.get("message")), http_status=status)
    if payload.get("api_version") != API_VERSION:
        return DecisionOutcome.failure("api_version_mismatch", http_status=status)
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    if expected_profile is not None and profile.get("id") != expected_profile:
        return DecisionOutcome.failure(
            "bad_response", "response profile does not match the request", http_status=status
        )
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        return DecisionOutcome.failure("bad_response", "response has no fields", http_status=status)
    fields: dict[str, FieldOutcome] = {}
    for name, raw in raw_fields.items():
        if not isinstance(name, str):
            continue
        raw = raw if isinstance(raw, dict) else {}
        status_text = raw.get("status")
        field_status = status_text if status_text in FIELD_STATUSES else "abstain"
        is_ok = field_status == "ok"
        calibrated = raw.get("calibrated") is True
        reasons = raw.get("reasons") if isinstance(raw.get("reasons"), list) else []
        fields[name] = FieldOutcome(
            name=name,
            status=field_status,
            value=raw.get("value") if is_ok else None,
            probability=_float(raw.get("probability")),
            coverage=_float(raw.get("coverage")),
            calibrated=calibrated,
            confidence=_optional_float(raw.get("confidence")) if calibrated else None,
            reasons=tuple(str(reason) for reason in reasons)
            + (() if status_text in FIELD_STATUSES else ("malformed_status",)),
            matched_from_transcript=_optional_str(raw.get("matched_from_transcript")),
        )
    fallback = payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    timings = payload.get("timings") if isinstance(payload.get("timings"), dict) else {}
    return DecisionOutcome(
        ok=True,
        http_status=status,
        fields=fields,
        language=_optional_str(payload.get("language")),
        no_speech_prob=_optional_float(payload.get("no_speech_prob")),
        fallback_used=fallback.get("used") is True,
        fallback_reason=_optional_str(fallback.get("reason")),
        transcript=_optional_str(fallback.get("transcript")),
        system2_required=fallback.get("system2_required") is True,
        total_ms=_optional_float(timings.get("total_ms")),
        provenance=DecisionProvenance(
            model=_optional_str(payload.get("model")),
            profile_id=_optional_str(profile.get("id")),
            profile_version=_optional_str(profile.get("version")),
            profile_status=_optional_str(profile.get("status")),
            n_encode=_optional_int(usage.get("n_encode")),
            n_encode_total=_optional_int(usage.get("n_encode_total")),
            fallback_provenance=_optional_str(fallback.get("provenance")),
        ),
    )


def _cancelled(token: BackendCancellationToken) -> DecisionOutcome:
    return DecisionOutcome.failure("deadline_exceeded" if token.reason_code == "timeout" else "cancelled")


def _multipart(parts: Mapping[str, Any]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in parts.items():
        chunks.append(f"--{boundary}\r\n".encode())
        if isinstance(value, tuple):
            chunks.append(f'Content-Disposition: form-data; name="{name}"; filename="{value[0]}"\r\n'.encode())
            chunks.append(b"Content-Type: application/octet-stream\r\n\r\n" + value[1] + b"\r\n")
        else:
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class AudioDecisionProvider:
    """Hub-facing AudioDecision specialist; every failure is a typed ``ok=False`` outcome."""

    def __init__(
        self,
        config: AudioDecisionConfig,
        *,
        transport: AudioDecisionTransport | None = None,
        decoder: AudioDecoder | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not config.enabled:
            raise AudioDecisionConfigurationError("audio decision provider requires VOICE_AUDIO_DECISION_ENABLED=true")
        config.validate()
        self._config = config
        self._base = config.url.rstrip("/")
        self._transport = transport or HttpClientTransport()
        self._decode_limits = AudioDecodeLimits(max_duration_ms=config.max_audio_ms)
        self._decoder = decoder or SafeAudioDecoder(limits=self._decode_limits)
        self._clock = clock

    def name(self) -> str:
        return "whisper_audio_decision"

    @property
    def allowed_profiles(self) -> tuple[str, ...]:
        return self._config.profiles

    def decide(
        self,
        *,
        filename: str,
        content: bytes,
        profile: str,
        fields: tuple[str, ...] | None = None,
        language: str = "auto",
        fallback: str = "none",
        cancellation_token: BackendCancellationToken | None = None,
    ) -> DecisionOutcome:
        started = self._clock()
        outcome = self._decide(
            filename=filename,
            content=content,
            profile=profile,
            fields=fields,
            language=language,
            fallback=fallback,
            cancellation_token=cancellation_token,
        )
        # Metadata only: no audio, transcript, labels or key.
        _log.info(
            "audio decision profile=%s ok=%s error=%s fields=%d n_encode=%s latency_ms=%.0f",
            profile if profile in self.allowed_profiles else "<rejected>",
            outcome.ok,
            outcome.error_code,
            len(outcome.fields),
            outcome.provenance.n_encode,
            (self._clock() - started) * 1000.0,
        )
        return outcome

    def _decide(
        self,
        *,
        filename: str,
        content: bytes,
        profile: str,
        fields: tuple[str, ...] | None,
        language: str,
        fallback: str,
        cancellation_token: BackendCancellationToken | None,
    ) -> DecisionOutcome:
        if profile not in self.allowed_profiles:
            return DecisionOutcome.failure("profile_not_allowed", "profile is not in VOICE_AUDIO_DECISION_PROFILES")
        if fallback not in FALLBACK_POLICIES:
            return DecisionOutcome.failure("invalid_request", "fallback must be none or transcribe")
        if profile in self._config.semantic_profiles:
            # Direct semantic scoring is at chance level: always hand a transcript to System-2.
            fallback = "transcribe"
        normalized_language = str(language or "auto").strip().lower()
        if not _LANGUAGE.match(normalized_language):
            return DecisionOutcome.failure("invalid_request", "language is invalid")
        if fields is not None and (not fields or len(fields) > 16 or not all(_FIELD_NAME.match(f) for f in fields)):
            return DecisionOutcome.failure("invalid_request", "fields are invalid")
        if cancellation_token is not None and cancellation_token.cancelled:
            return _cancelled(cancellation_token)

        # Ananta's decoding and limits run before anything leaves the process.
        if not content:
            return DecisionOutcome.failure("invalid_audio", "audio is empty")
        try:
            audio = self._decoder.decode(filename=filename or "audio.wav", payload=content)
        except AudioDecodeError as exc:
            code = "audio_too_long" if exc.code == "decode.duration_limit" else "invalid_audio"
            return DecisionOutcome.failure(code, f"audio rejected before upload ({exc.code})")
        except Exception:  # decoder failures never become a decision
            return DecisionOutcome.failure("invalid_audio", "audio could not be decoded")
        if audio.duration_ms > self._config.max_audio_ms:
            return DecisionOutcome.failure("audio_too_long", "audio decision clips are limited to 30 s")
        if audio.duration_ms <= 0 or not audio.pcm_s16le:
            return DecisionOutcome.failure("invalid_audio", "audio is empty")

        timeout_s = self._config.timeout_ms / 1000.0
        if cancellation_token is not None:
            if cancellation_token.cancelled:
                return _cancelled(cancellation_token)
            timeout_s = cancellation_token.remaining_seconds(maximum=timeout_s)
            if timeout_s <= 0.0:
                return DecisionOutcome.failure("deadline_exceeded")

        request: dict[str, Any] = {
            "profile": profile,
            "options": {
                "language": normalized_language,
                "fallback": fallback,
                "timeout_ms": max(1, int(timeout_s * 1000)),
            },
        }
        if fields:
            request["fields"] = list(fields)
        body, content_type = _multipart({"file": ("audio.wav", audio.to_wav_bytes()), "request": json.dumps(request)})
        status, response = self._send("POST", DECISIONS_PATH, body=body, content_type=content_type, timeout_s=timeout_s)
        if cancellation_token is not None and cancellation_token.cancelled:
            return _cancelled(cancellation_token)
        if status is None:
            return DecisionOutcome.failure(response.decode("ascii", errors="replace") or "unavailable")
        return parse_decision_response(status, response, expected_profile=profile)

    def profiles(self) -> list[dict[str, Any]]:
        """Loaded server profiles, restricted to the allow-list; ``[]`` on any error."""
        status, response = self._send(
            "GET", PROFILES_PATH, body=None, content_type=None, timeout_s=self._config.timeout_ms / 1000.0
        )
        if status != 200:
            return []
        try:
            data = json.loads(response).get("data", [])
        except (ValueError, UnicodeError, AttributeError):
            return []
        allowed = set(self.allowed_profiles)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("id") in allowed]

    def _send(
        self, method: str, path: str, *, body: bytes | None, content_type: str | None, timeout_s: float
    ) -> tuple[int | None, bytes]:
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            return self._transport.request(method, self._base + path, body=body, headers=headers, timeout_s=timeout_s)
        except (TimeoutError, socket.timeout):
            return None, b"deadline_exceeded"
        except (OSError, http.client.HTTPException, ValueError):
            return None, b"unavailable"


def build_audio_decision_provider(
    config: AudioDecisionConfig | None = None,
    **kwargs: Any,
) -> AudioDecisionProvider | None:
    """``None`` when the feature is off; the service is then never contacted."""
    resolved = config if config is not None else AudioDecisionConfig.from_env()
    if not resolved.enabled:
        return None
    return AudioDecisionProvider(resolved, **kwargs)
