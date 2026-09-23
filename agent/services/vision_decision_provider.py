"""Optional VisionDecision specialist backed by the llama.cpp-vision-decision fork.

The fork (``vendor/llama.cpp-vision-decision``) runs as a separate ``llama-server`` process with a vision
model (``--mmproj``) and ``--decision-seqs``. Ananta talks HTTP only (``POST /v1/decision``); there is no
linking and no shared memory. Rules:

* every outcome is a :class:`VisionDecisionOutcome`; transport errors, timeouts, HTTP errors and
  malformed or schema-invalid responses are ``ok=False`` and carry no value, never a default label;
* Ananta always sends abstain thresholds and re-checks them locally; a field the server lists in
  ``abstained`` (or that fails the local check) carries no value and must be escalated;
* a decision value is advisory and never grants a permission; the hub owns policy and fallback.

Images are validated and bounded locally (count, bytes, pixels, side length) and re-encoded as PNG
(drops metadata) before anything leaves the process; only ``data:`` URIs are sent, never URLs.
Images, base64, prompt text, decision values and the API key are never logged.
"""
from __future__ import annotations

import base64
import dataclasses
import http.client
import io
import ipaddress
import json
import logging
import math
import os
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

_log = logging.getLogger(__name__)

DECISION_PATH = "/v1/decision"
FIELD_TYPES = frozenset({"enum", "boolean", "integer", "number"})
MAX_FIELDS = 32  # fork: 1-32 fields
MAX_CHOICES = 255  # fork: 1-255 allowed values per field
MAX_CONTEXTS = 16
MAX_MEDIA_LIMIT = 16  # fork default --decision-max-media
MAX_IMAGE_BYTES_LIMIT = 10 * 1024 * 1024  # fork: remote media limit 10 MB
MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard before decoding
MAX_TEXT_CHARS = 4_000
ALLOWED_IMAGE_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})

_ENV_KEYS = (
    "VISION_DECISION_ENABLED",
    "VISION_DECISION_URL",
    "VISION_DECISION_API_KEY_FILE",
    "VISION_DECISION_API_KEY",
    "VISION_DECISION_TIMEOUT_MS",
    "VISION_DECISION_MAX_MEDIA",
    "VISION_DECISION_MAX_IMAGE_BYTES",
    "VISION_DECISION_MAX_IMAGE_SIDE",
    "VISION_DECISION_MODELS",
    "VISION_DECISION_SCHEMAS",
    "VISION_DECISION_MIN_PROBABILITY",
    "VISION_DECISION_MIN_MARGIN",
)
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SCHEMA_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_SECRET_BYTES = 16 * 1024
_SECRET_ROOTS = (Path("/run/secrets"),)


class VisionDecisionConfigurationError(ValueError):
    """Invalid VisionDecision settings; raised only when the feature is enabled."""


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in str(value or "").split(",") if item.strip()))


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = str(env.get(name) or default).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise VisionDecisionConfigurationError(f"{name} must be an integer") from exc


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = str(env.get(name) or default).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise VisionDecisionConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise VisionDecisionConfigurationError(f"{name} must be finite")
    return value


def _read_secret_file(path_text: str, *, allowed_roots: tuple[Path, ...]) -> str:
    try:
        resolved = Path(path_text).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VisionDecisionConfigurationError("VISION_DECISION_API_KEY_FILE cannot be read") from exc
    if allowed_roots and not any(resolved.is_relative_to(root.resolve()) for root in allowed_roots):
        raise VisionDecisionConfigurationError("VISION_DECISION_API_KEY_FILE must live in the secret store")
    if not resolved.is_file() or resolved.stat().st_size > _MAX_SECRET_BYTES:
        raise VisionDecisionConfigurationError("VISION_DECISION_API_KEY_FILE is not a small regular file")
    try:
        return resolved.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise VisionDecisionConfigurationError("VISION_DECISION_API_KEY_FILE cannot be read") from exc


def _is_local_host(host: str) -> bool:
    if host == "localhost" or "." not in host:
        # loopback name or single-label compose service name
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "host.docker.internal"
    return address.is_loopback or address.is_private


@dataclass(frozen=True)
class VisionDecisionConfig:
    enabled: bool = False
    url: str = "http://127.0.0.1:8096"
    api_key: str | None = field(default=None, repr=False)
    timeout_ms: int = 30_000  # CPU build: one image + 4 fields takes ~3-7 s on Qwen3-VL-2B
    max_media: int = 4
    max_image_bytes: int = 4 * 1024 * 1024
    max_image_side: int = 1024
    models: tuple[str, ...] = ()  # empty: any model the server reports
    schemas: tuple[str, ...] = ()  # empty: any schema id
    min_probability: float = 0.8
    min_margin: float = 0.3

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        secret_roots: tuple[Path, ...] = _SECRET_ROOTS,
    ) -> "VisionDecisionConfig":
        env = os.environ if environ is None else environ
        if not _as_bool(env.get("VISION_DECISION_ENABLED")):
            # Feature off: read nothing else, not even the secret file.
            return cls(enabled=False)
        key_file = str(env.get("VISION_DECISION_API_KEY_FILE") or "").strip()
        api_key = (
            _read_secret_file(key_file, allowed_roots=secret_roots)
            if key_file
            else str(env.get("VISION_DECISION_API_KEY") or "").strip()
        )
        config = cls(
            enabled=True,
            url=str(env.get("VISION_DECISION_URL") or cls.url).strip(),
            api_key=api_key or None,
            timeout_ms=_env_int(env, "VISION_DECISION_TIMEOUT_MS", cls.timeout_ms),
            max_media=_env_int(env, "VISION_DECISION_MAX_MEDIA", cls.max_media),
            max_image_bytes=_env_int(env, "VISION_DECISION_MAX_IMAGE_BYTES", cls.max_image_bytes),
            max_image_side=_env_int(env, "VISION_DECISION_MAX_IMAGE_SIDE", cls.max_image_side),
            models=_csv(env.get("VISION_DECISION_MODELS")),
            schemas=_csv(env.get("VISION_DECISION_SCHEMAS")),
            min_probability=_env_float(env, "VISION_DECISION_MIN_PROBABILITY", cls.min_probability),
            min_margin=_env_float(env, "VISION_DECISION_MIN_MARGIN", cls.min_margin),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.enabled:
            return
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise VisionDecisionConfigurationError("VISION_DECISION_URL must be http(s)://host[:port]")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise VisionDecisionConfigurationError(
                "VISION_DECISION_URL must not carry credentials, a path, query or fragment"
            )
        if parsed.scheme == "http" and not _is_local_host(parsed.hostname):
            raise VisionDecisionConfigurationError("VISION_DECISION_URL needs https for non-local hosts")
        if self.api_key is not None and (len(self.api_key) < 16 or any(ch.isspace() for ch in self.api_key)):
            raise VisionDecisionConfigurationError("VISION_DECISION_API_KEY(_FILE) needs at least 16 non-space characters")
        if not 100 <= self.timeout_ms <= 300_000:
            raise VisionDecisionConfigurationError("VISION_DECISION_TIMEOUT_MS must be between 100 and 300000")
        if not 1 <= self.max_media <= MAX_MEDIA_LIMIT:
            raise VisionDecisionConfigurationError(f"VISION_DECISION_MAX_MEDIA must be between 1 and {MAX_MEDIA_LIMIT}")
        if not 1024 <= self.max_image_bytes <= MAX_IMAGE_BYTES_LIMIT:
            raise VisionDecisionConfigurationError("VISION_DECISION_MAX_IMAGE_BYTES must be between 1 KiB and 10 MiB")
        if not 32 <= self.max_image_side <= 4096:
            raise VisionDecisionConfigurationError("VISION_DECISION_MAX_IMAGE_SIDE must be between 32 and 4096")
        if not all(_MODEL_ID.match(model) for model in self.models):
            raise VisionDecisionConfigurationError("VISION_DECISION_MODELS contains an invalid model id")
        if not all(_SCHEMA_ID.match(schema) for schema in self.schemas):
            raise VisionDecisionConfigurationError("VISION_DECISION_SCHEMAS contains an invalid schema id")
        # Thresholds of 0 would switch abstain off on the server: an escalation signal must always exist.
        if not 0.0 < self.min_probability <= 1.0 or not 0.0 < self.min_margin <= 1.0:
            raise VisionDecisionConfigurationError(
                "VISION_DECISION_MIN_PROBABILITY and VISION_DECISION_MIN_MARGIN must be in (0, 1]"
            )


# ---------------------------------------------------------------------------------------------------------
# Request types


@dataclass(frozen=True)
class VisionField:
    """One finite schema field. Free text (``string``/OCR) is not a decision and is rejected."""

    name: str
    type: str
    description: str
    choices: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    temperature: float | None = None

    def allowed(self, value: Any) -> bool:
        if self.type == "boolean":
            return isinstance(value, bool)
        if self.type == "enum":
            return any(value == choice and type(value) is type(choice) for choice in self.choices)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        if self.type == "integer" and (not float(value).is_integer()):
            return False
        assert self.minimum is not None and self.maximum is not None
        if not self.minimum - 1e-9 <= value <= self.maximum + 1e-9:
            return False
        if self.type == "number" and self.step:
            steps = (value - self.minimum) / self.step
            return abs(steps - round(steps)) < 1e-6
        return True

    def as_request(self) -> dict[str, Any]:
        spec: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.type == "enum":
            spec["choices"] = list(self.choices)
        if self.type in {"integer", "number"}:
            spec["minimum"] = int(self.minimum) if self.type == "integer" else self.minimum
            spec["maximum"] = int(self.maximum) if self.type == "integer" else self.maximum
        if self.type == "number" and self.step is not None:
            spec["step"] = self.step
        if self.temperature is not None:
            # The fork takes per-field temperatures in the field spec; the top-level key is one number.
            spec["temperature"] = self.temperature
        return spec


@dataclass(frozen=True)
class VisionDecisionSchema:
    schema_id: str
    fields: tuple[VisionField, ...]
    instructions: str = field(default="", repr=False)

    @classmethod
    def from_mapping(
        cls, schema_id: str, spec: Mapping[str, Any], *, instructions: str = ""
    ) -> "VisionDecisionSchema":
        """Build and validate a schema from the fork's compact form; raises ``ValueError``."""
        if not isinstance(spec, Mapping):
            raise ValueError("schema must be an object")
        fields = []
        for name, raw in spec.items():
            if not isinstance(raw, Mapping):
                raise ValueError("every schema field must be an object")
            kind = raw.get("type")
            if kind == "string":
                raise ValueError("free-text fields are not decisions; use a chat completion (no OCR here)")
            fields.append(
                VisionField(
                    name=str(name),
                    type=str(kind),
                    description=str(raw.get("description") or ""),
                    choices=tuple(raw.get("choices") or raw.get("enum") or ()),
                    minimum=raw.get("minimum"),
                    maximum=raw.get("maximum"),
                    step=raw.get("step"),
                    temperature=raw.get("temperature"),
                )
            )
        schema = cls(schema_id=schema_id, fields=tuple(fields), instructions=instructions)
        schema.validate()
        return schema

    def validate(self) -> None:
        if not _SCHEMA_ID.match(self.schema_id):
            raise ValueError("schema id is invalid")
        if not 1 <= len(self.fields) <= MAX_FIELDS:
            raise ValueError(f"a schema needs 1-{MAX_FIELDS} fields")
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("schema field names must be unique")
        if len(self.instructions) > MAX_TEXT_CHARS:
            raise ValueError("instructions are too long")
        for f in self.fields:
            if not _FIELD_NAME.match(f.name):
                raise ValueError("schema field name is invalid")
            if f.type not in FIELD_TYPES:
                raise ValueError("schema field type must be enum, boolean, integer or number")
            if not f.description.strip() or len(f.description) > 500:
                raise ValueError("every schema field needs a short description")
            if f.temperature is not None and (
                isinstance(f.temperature, bool) or not isinstance(f.temperature, (int, float)) or not 0 < f.temperature <= 100
            ):
                raise ValueError("field temperature must be a number in (0, 100]")
            if f.type == "enum":
                if not 1 <= len(f.choices) <= MAX_CHOICES or len(set(map(repr, f.choices))) != len(f.choices):
                    raise ValueError("enum fields need 1-255 unique choices")
                if not all(isinstance(c, str) and 0 < len(c) <= 100 for c in f.choices):
                    raise ValueError("enum choices must be short strings")
            elif f.type in {"integer", "number"}:
                numbers = (f.minimum, f.maximum) + ((f.step,) if f.step is not None else ())
                if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
                    raise ValueError("numeric fields need finite minimum and maximum")
                assert f.minimum is not None and f.maximum is not None
                if f.minimum > f.maximum:
                    raise ValueError("minimum must not exceed maximum")
                if f.type == "integer":
                    if not (float(f.minimum).is_integer() and float(f.maximum).is_integer()):
                        raise ValueError("integer bounds must be integers")
                    if f.maximum - f.minimum + 1 > MAX_CHOICES:
                        raise ValueError("integer fields allow at most 255 values")
                elif f.step is None or f.step <= 0 or (f.maximum - f.minimum) / f.step + 1 > MAX_CHOICES + 1e-9:
                    raise ValueError("number fields need a positive step and at most 255 grid values")

    def field(self, name: str) -> VisionField | None:
        return next((f for f in self.fields if f.name == name), None)

    def as_request(self) -> dict[str, Any]:
        return {f.name: f.as_request() for f in self.fields}


@dataclass(frozen=True)
class VisionImage:
    content: bytes = field(repr=False)
    filename: str = "image"


@dataclass(frozen=True)
class VisionContext:
    images: tuple[VisionImage, ...]
    text: str = field(default="", repr=False)


# ---------------------------------------------------------------------------------------------------------
# Outcome types


@dataclass(frozen=True)
class VisionFieldOutcome:
    name: str
    value: Any  # None when abstained: an abstained field never carries a value
    top_value: Any = field(repr=False)  # server's most likely value; a hint for escalation only
    probability: float
    margin: float | None  # tree fields only
    entropy: float | None  # nats, tree fields only
    abstain: bool
    abstain_reasons: tuple[str, ...] = ()
    tree: bool | None = None
    scored_nodes: int | None = None
    temperature: float | None = None
    probs: tuple[tuple[Any, float], ...] = field(default=(), repr=False)

    @property
    def accepted(self) -> bool:
        return not self.abstain and self.value is not None

    def as_audit_dict(self) -> dict[str, Any]:
        """No value, no label."""
        return {
            "field": self.name,
            "accepted": self.accepted,
            "probability": self.probability,
            "margin": self.margin,
            "entropy": self.entropy,
            "abstain": self.abstain,
            "abstain_reasons": list(self.abstain_reasons),
        }


@dataclass(frozen=True)
class VisionContextResult:
    index: int
    fields: Mapping[str, VisionFieldOutcome]
    abstained: tuple[str, ...]
    context_tokens: int | None = None
    scored_rows: int | None = None

    @property
    def accepted(self) -> dict[str, Any]:
        return {name: item.value for name, item in self.fields.items() if item.accepted}


@dataclass(frozen=True)
class VisionUsage:
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    context_tokens: int | None = None
    scored_rows: int | None = None
    media_chunks: int | None = None
    media_tokens: int | None = None
    media_cached: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class VisionTimings:
    prefill_ms: float | None = None
    media_encode_ms: float | None = None
    scoring_ms: float | None = None
    total_ms: float | None = None
    rounds: int | None = None
    per_decision_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class VisionDecisionOutcome:
    ok: bool  # False: no decision at all (error_code tells why), never a default label
    error_code: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    schema_id: str | None = None
    model: str | None = None
    results: tuple[VisionContextResult, ...] = ()
    usage: VisionUsage = field(default_factory=VisionUsage)
    timings: VisionTimings = field(default_factory=VisionTimings)
    latency_ms: float | None = None
    grants_permission: bool = False

    @classmethod
    def failure(
        cls, code: str, message: str | None = None, *, http_status: int | None = None, schema_id: str | None = None
    ) -> "VisionDecisionOutcome":
        return cls(ok=False, error_code=code, error_message=message, http_status=http_status, schema_id=schema_id)

    def provenance(self) -> dict[str, Any]:
        return {
            "provider": "llama.cpp-vision-decision",
            "endpoint": DECISION_PATH,
            "model": self.model,
            "schema_id": self.schema_id,
            "usage": self.usage.as_dict(),
            "timings": self.timings.as_dict(),
        }

    def as_audit_dict(self) -> dict[str, Any]:
        """Metadata only: no image, prompt, value or label."""
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "http_status": self.http_status,
            "contexts": [
                {"index": r.index, "abstained": len(r.abstained), "fields": [f.as_audit_dict() for f in r.fields.values()]}
                for r in self.results
            ],
            "provenance": self.provenance(),
            "grants_permission": False,
        }


# ---------------------------------------------------------------------------------------------------------
# Transport


class VisionDecisionTransport(Protocol):
    def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str], timeout_s: float
    ) -> tuple[int, bytes]: ...


class HttpClientTransport:
    """Stdlib HTTP transport: no redirects, no proxies, bounded response body."""

    def request(
        self, method: str, url: str, *, body: bytes | None, headers: Mapping[str, str], timeout_s: float
    ) -> tuple[int, bytes]:
        parsed = urllib.parse.urlparse(url)
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=timeout_s)
        try:
            connection.request(method, parsed.path or "/", body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(data) > _MAX_RESPONSE_BYTES:
                raise http.client.HTTPException("vision decision response too large")
            return response.status, data
        finally:
            connection.close()


# ---------------------------------------------------------------------------------------------------------
# Local image validation


class ImageRejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def prepare_image(content: bytes, *, max_bytes: int, max_side: int) -> bytes:
    """Validate an image locally and return it re-encoded as PNG (no metadata), downscaled to ``max_side``."""
    if not content:
        raise ImageRejected("invalid_image", "image is empty")
    if len(content) > max_bytes:
        raise ImageRejected("image_too_large", "image exceeds VISION_DECISION_MAX_IMAGE_BYTES")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # fail closed: no unvalidated upload
        raise ImageRejected("image_decoder_unavailable", "Pillow is required to validate images") from exc
    try:
        with Image.open(io.BytesIO(content)) as probe:
            fmt = probe.format
            width, height = probe.size
            frames = getattr(probe, "n_frames", 1)
            if fmt not in ALLOWED_IMAGE_FORMATS:
                raise ImageRejected("invalid_image", "only PNG, JPEG and WebP images are accepted")
            if frames != 1:
                raise ImageRejected("invalid_image", "animated images are not accepted")
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ImageRejected("image_too_large", "image dimensions are out of bounds")
            probe.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            converted = image.convert("RGB")
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ImageRejected("invalid_image", "image could not be decoded") from exc
    if max(converted.size) > max_side:
        converted.thumbnail((max_side, max_side))
    out = io.BytesIO()
    converted.save(out, format="PNG")
    return out.getvalue()


# ---------------------------------------------------------------------------------------------------------
# Response parsing


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _error_code(status: int) -> str:
    if status == 400:
        return "invalid_request"
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status == 503:
        return "unavailable"
    return f"http_{status}"


def _error_message(payload: Any) -> str | None:
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    # The server's message may quote request details; keep it short and never log it.
    return message[:300] if isinstance(message, str) else None


def _model_allowed(model: str | None, allowlist: tuple[str, ...]) -> bool:
    if not allowlist:
        return True
    if not model:
        return False
    return model in allowlist or os.path.basename(model) in allowlist


def _parse_field(
    spec: VisionField, raw: Any, *, listed_abstain: bool, min_probability: float, min_margin: float
) -> VisionFieldOutcome | None:
    if not isinstance(raw, dict) or "value" not in raw:
        return None
    top_value = raw.get("value")
    if not spec.allowed(top_value):
        return None  # schema-invalid value: the whole answer is untrusted
    probability = _optional_float(raw.get("probability"))
    if probability is None or not 0.0 <= probability <= 1.0 + 1e-6:
        return None
    margin = _optional_float(raw.get("margin"))
    entropy = _optional_float(raw.get("entropy"))
    reasons: list[str] = []
    server_abstain = raw.get("abstain")
    if server_abstain is True or listed_abstain:
        reasons.append("server_abstain")
    elif server_abstain is not False:
        reasons.append("abstain_missing")  # thresholds were sent, so the server must answer this
    if probability < min_probability:
        reasons.append("low_probability")
    if margin is not None and margin < min_margin:
        reasons.append("low_margin")
    probs: list[tuple[Any, float]] = []
    for entry in raw.get("probs") or ():
        if isinstance(entry, dict) and spec.allowed(entry.get("value")):
            p = _optional_float(entry.get("probability"))
            if p is not None:
                probs.append((entry.get("value"), p))
    abstain = bool(reasons)
    return VisionFieldOutcome(
        name=spec.name,
        value=None if abstain else top_value,
        top_value=top_value,
        probability=probability,
        margin=margin,
        entropy=entropy,
        abstain=abstain,
        abstain_reasons=tuple(dict.fromkeys(reasons)),
        tree=raw.get("tree") if isinstance(raw.get("tree"), bool) else None,
        scored_nodes=_optional_int(raw.get("scored_nodes")),
        temperature=spec.temperature,
        probs=tuple(probs),
    )


def parse_decision_response(
    status: int,
    body: bytes,
    *,
    schema: VisionDecisionSchema,
    n_contexts: int,
    min_probability: float,
    min_margin: float,
    models: tuple[str, ...] = (),
) -> VisionDecisionOutcome:
    """Map an HTTP answer to a fail-closed :class:`VisionDecisionOutcome`."""
    sid = schema.schema_id
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        payload = None
    if status != 200:
        return VisionDecisionOutcome.failure(_error_code(status), _error_message(payload), http_status=status, schema_id=sid)
    if not isinstance(payload, dict) or payload.get("object") != "decision":
        return VisionDecisionOutcome.failure("bad_response", "response is not a decision object", http_status=status, schema_id=sid)
    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    if not _model_allowed(model, models):
        return VisionDecisionOutcome.failure("model_not_allowed", "server model is not in VISION_DECISION_MODELS", http_status=status, schema_id=sid)
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != n_contexts:
        return VisionDecisionOutcome.failure("bad_response", "result count does not match the contexts", http_status=status, schema_id=sid)
    names = {f.name for f in schema.fields}
    results: list[VisionContextResult] = []
    for index, raw in enumerate(raw_results):
        raw_fields = raw.get("fields") if isinstance(raw, dict) else None
        abstained_raw = raw.get("abstained") if isinstance(raw, dict) else None
        if not isinstance(raw_fields, dict) or set(raw_fields) != names or not isinstance(abstained_raw, list):
            return VisionDecisionOutcome.failure("bad_response", "result fields do not match the schema", http_status=status, schema_id=sid)
        listed = {name for name in abstained_raw if isinstance(name, str)}
        if not listed <= names:
            return VisionDecisionOutcome.failure("bad_response", "abstained lists unknown fields", http_status=status, schema_id=sid)
        decision = raw.get("decision")
        fields: dict[str, VisionFieldOutcome] = {}
        for spec in schema.fields:
            item = _parse_field(
                spec,
                raw_fields[spec.name],
                listed_abstain=spec.name in listed,
                min_probability=min_probability,
                min_margin=min_margin,
            )
            if item is None or not isinstance(decision, dict) or decision.get(spec.name) != item.top_value:
                return VisionDecisionOutcome.failure("bad_response", "a field is malformed or not schema-valid", http_status=status, schema_id=sid)
            fields[spec.name] = item
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        results.append(
            VisionContextResult(
                index=index,
                fields=fields,
                abstained=tuple(name for name in fields if fields[name].abstain),
                context_tokens=_optional_int(usage.get("context_tokens")),
                scored_rows=_optional_int(usage.get("scored_rows")),
            )
        )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    timings = payload.get("timings") if isinstance(payload.get("timings"), dict) else {}
    return VisionDecisionOutcome(
        ok=True,
        http_status=status,
        schema_id=sid,
        model=model,
        results=tuple(results),
        usage=VisionUsage(**{key: _optional_int(usage.get(key)) for key in VisionUsage.__dataclass_fields__}),
        timings=VisionTimings(
            prefill_ms=_optional_float(timings.get("prefill_ms")),
            media_encode_ms=_optional_float(timings.get("media_encode_ms")),
            scoring_ms=_optional_float(timings.get("scoring_ms")),
            total_ms=_optional_float(timings.get("total_ms")),
            rounds=_optional_int(timings.get("rounds")),
            per_decision_ms=_optional_float(timings.get("per_decision_ms")),
        ),
    )


# ---------------------------------------------------------------------------------------------------------
# Provider


class VisionDecisionProvider:
    """Hub-facing VisionDecision specialist; every failure is a typed ``ok=False`` outcome."""

    def __init__(
        self,
        config: VisionDecisionConfig,
        *,
        transport: VisionDecisionTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not config.enabled:
            raise VisionDecisionConfigurationError("VisionDecisionProvider needs VISION_DECISION_ENABLED=true")
        config.validate()
        self._config = config
        self._transport = transport or HttpClientTransport()
        self._clock = clock

    @property
    def config(self) -> VisionDecisionConfig:
        return self._config

    def decide(
        self,
        schema: VisionDecisionSchema,
        contexts: Sequence[VisionContext],
        *,
        return_probs: bool = False,
        deadline_monotonic: float | None = None,
    ) -> VisionDecisionOutcome:
        started = self._clock()
        try:
            outcome = self._decide(schema, contexts, return_probs=return_probs, deadline_monotonic=deadline_monotonic)
        except Exception:  # a provider bug never becomes a decision
            _log.exception("vision decision provider failed")
            outcome = VisionDecisionOutcome.failure("provider_error", schema_id=getattr(schema, "schema_id", None))
        latency_ms = (self._clock() - started) * 1000.0
        outcome = dataclasses.replace(outcome, latency_ms=latency_ms)
        # Metadata only: no image, base64, prompt, value or key.
        _log.info(
            "vision decision schema=%s ok=%s error=%s status=%s contexts=%d abstained=%d media_tokens=%s "
            "media_cached=%s total_ms=%s latency_ms=%.0f",
            outcome.schema_id if outcome.schema_id and _SCHEMA_ID.match(outcome.schema_id) else "<rejected>",
            outcome.ok,
            outcome.error_code,
            outcome.http_status,
            len(outcome.results),
            sum(len(r.abstained) for r in outcome.results),
            outcome.usage.media_tokens,
            outcome.usage.media_cached,
            outcome.timings.total_ms,
            latency_ms,
        )
        return outcome

    def _decide(
        self,
        schema: VisionDecisionSchema,
        contexts: Sequence[VisionContext],
        *,
        return_probs: bool,
        deadline_monotonic: float | None,
    ) -> VisionDecisionOutcome:
        sid = schema.schema_id if isinstance(schema, VisionDecisionSchema) else None
        if not isinstance(schema, VisionDecisionSchema):
            return VisionDecisionOutcome.failure("invalid_request", "schema must be a VisionDecisionSchema")
        try:
            schema.validate()
        except ValueError as exc:
            return VisionDecisionOutcome.failure("invalid_schema", str(exc), schema_id=sid)
        if self._config.schemas and schema.schema_id not in self._config.schemas:
            return VisionDecisionOutcome.failure("schema_not_allowed", "schema is not in VISION_DECISION_SCHEMAS", schema_id=sid)
        if not contexts or len(contexts) > MAX_CONTEXTS:
            return VisionDecisionOutcome.failure("invalid_request", f"1-{MAX_CONTEXTS} contexts are required", schema_id=sid)
        n_images = sum(len(c.images) for c in contexts)
        if n_images > self._config.max_media:
            return VisionDecisionOutcome.failure("too_many_images", "request exceeds VISION_DECISION_MAX_MEDIA", schema_id=sid)

        # Local validation and re-encoding run before anything leaves the process.
        request_contexts: list[list[dict[str, Any]]] = []
        for context in contexts:
            if not context.images:
                return VisionDecisionOutcome.failure("invalid_request", "every context needs at least one image", schema_id=sid)
            if len(context.text) > MAX_TEXT_CHARS:
                return VisionDecisionOutcome.failure("invalid_request", "context text is too long", schema_id=sid)
            parts: list[dict[str, Any]] = []
            for image in context.images:
                try:
                    png = prepare_image(
                        image.content, max_bytes=self._config.max_image_bytes, max_side=self._config.max_image_side
                    )
                except ImageRejected as exc:
                    return VisionDecisionOutcome.failure(exc.code, f"image rejected before upload: {exc}", schema_id=sid)
                url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": url}})
            if context.text.strip():
                parts.append({"type": "text", "text": context.text})
            request_contexts.append(parts)

        timeout_s = self._config.timeout_ms / 1000.0
        if deadline_monotonic is not None:
            timeout_s = min(timeout_s, deadline_monotonic - self._clock())
            if timeout_s <= 0.0:
                return VisionDecisionOutcome.failure("deadline_exceeded", schema_id=sid)
        request: dict[str, Any] = {
            "schema": schema.as_request(),
            "contexts": request_contexts,
            # Always send thresholds: the server marks and lists uncertain fields, Ananta re-checks them.
            "abstain": {"min_probability": self._config.min_probability, "min_margin": self._config.min_margin},
            "return_probs": bool(return_probs),
        }
        if schema.instructions:
            request["instructions"] = schema.instructions
        if len(self._config.models) == 1:
            request["model"] = self._config.models[0]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        try:
            status, body = self._transport.request(
                "POST",
                self._config.url.rstrip("/") + DECISION_PATH,
                body=json.dumps(request).encode("utf-8"),
                headers=headers,
                timeout_s=timeout_s,
            )
        except (socket.timeout, TimeoutError):
            return VisionDecisionOutcome.failure("deadline_exceeded", "vision decision service timed out", schema_id=sid)
        except (OSError, http.client.HTTPException):
            return VisionDecisionOutcome.failure("unavailable", "vision decision service not reachable", schema_id=sid)
        return parse_decision_response(
            status,
            body,
            schema=schema,
            n_contexts=len(request_contexts),
            min_probability=self._config.min_probability,
            min_margin=self._config.min_margin,
            models=self._config.models,
        )


def build_vision_decision_provider(
    config: VisionDecisionConfig, *, transport: VisionDecisionTransport | None = None
) -> VisionDecisionProvider | None:
    """``None`` when the feature is off; nothing is contacted."""
    if not config.enabled:
        return None
    return VisionDecisionProvider(config, transport=transport)


_provider_lock = threading.Lock()
_provider_cache: tuple[tuple[str | None, ...], VisionDecisionProvider] | None = None


def get_vision_decision_provider(environ: Mapping[str, str] | None = None) -> VisionDecisionProvider | None:
    """``None`` when the feature is off. Raises ``VisionDecisionConfigurationError`` if misconfigured."""
    global _provider_cache
    env = os.environ if environ is None else environ
    config = VisionDecisionConfig.from_env(env)
    if not config.enabled:
        return None
    key = tuple(env.get(name) for name in _ENV_KEYS)
    with _provider_lock:
        if _provider_cache is None or _provider_cache[0] != key:
            _provider_cache = (key, VisionDecisionProvider(config))
        return _provider_cache[1]
