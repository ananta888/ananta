"""Shared transport-neutral contracts for bounded local Voice judging."""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

STRICT_CHOICE_OPERATIONS = frozenset({"classification", "masked_token", "reranking"})


@dataclass(frozen=True)
class StrictChoice:
    choice_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.choice_id or len(self.choice_id) > 128 or self.choice_id.strip() != self.choice_id:
            raise ValueError("strict choice ID is invalid")
        if not self.text or len(self.text) > 8_000 or "\x00" in self.text:
            raise ValueError("strict choice text is invalid")


@dataclass(frozen=True)
class StrictChoiceRequest:
    region_id: str
    operation: str
    choices: tuple[StrictChoice, ...]
    baseline_choice_id: str

    def __post_init__(self) -> None:
        if not self.region_id or len(self.region_id) > 128:
            raise ValueError("strict choice region ID is invalid")
        if self.operation not in STRICT_CHOICE_OPERATIONS:
            raise ValueError("restricted judge operation is not allowed")
        if len(self.choices) < 1 or len(self.choices) > 64:
            raise ValueError("strict choice count is outside its bounds")
        choice_ids = tuple(choice.choice_id for choice in self.choices)
        if len(set(choice_ids)) != len(choice_ids):
            raise ValueError("strict choice IDs must be unique")
        if self.baseline_choice_id not in choice_ids:
            raise ValueError("baseline choice is unknown")

    def as_restricted_payload(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "kind": "restricted_strict_choice",
            "no_generation": True,
            "operation": self.operation,
            "region_id": self.region_id,
            "choices": [{"choice_id": choice.choice_id, "text": choice.text} for choice in self.choices],
            "baseline_choice_id": self.baseline_choice_id,
        }


@dataclass(frozen=True)
class StrictChoiceOutcome:
    choice_id: str
    text: str
    status: str
    reason_code: str | None
    scores: Mapping[str, float]
    execution_path: str = "restricted_strict_choice"
    no_generation: bool = True


class RestrictedChoiceExecutor(Protocol):
    def execute(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class StrictChoiceJudge:
    """Restricted no-generation judge with exact deterministic fallback."""

    _RESPONSE_KEYS = frozenset({"schema_version", "operation", "no_generation", "choice_id", "scores"})

    def __init__(self, executor: RestrictedChoiceExecutor) -> None:
        self._executor = executor

    def evaluate(self, request: StrictChoiceRequest) -> StrictChoiceOutcome:
        choices = {choice.choice_id: choice.text for choice in request.choices}
        try:
            response = self._executor.execute(request.as_restricted_payload())
            choice_id, scores = self._validate_response(request, response)
        except Exception:
            return _strict_fallback(request, "restricted_judge_failed")
        return StrictChoiceOutcome(
            choice_id=choice_id,
            text=choices[choice_id],
            status="selected",
            reason_code=None,
            scores=scores,
        )

    def _validate_response(
        self,
        request: StrictChoiceRequest,
        response: Mapping[str, object],
    ) -> tuple[str, dict[str, float]]:
        if not isinstance(response, Mapping):
            raise ValueError("restricted judge response must be an object")
        if set(response) - self._RESPONSE_KEYS:
            raise ValueError("restricted judge response contains forbidden fields")
        if response.get("schema_version", "1.0") != "1.0":
            raise ValueError("restricted judge schema version is unsupported")
        if response.get("no_generation") is not True:
            raise ValueError("restricted judge did not prove no_generation")
        if response.get("operation") != request.operation:
            raise ValueError("restricted judge operation does not match")
        known_ids = {choice.choice_id for choice in request.choices}
        raw_scores = response.get("scores", {})
        if not isinstance(raw_scores, Mapping):
            raise ValueError("restricted judge scores must be an object")
        scores: dict[str, float] = {}
        for raw_id, raw_score in raw_scores.items():
            choice_id = str(raw_id)
            if choice_id not in known_ids or isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise ValueError("restricted judge score references an invalid choice")
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError("restricted judge score must be finite")
            scores[choice_id] = score
        raw_choice_id = response.get("choice_id")
        if raw_choice_id is None:
            if not scores:
                raise ValueError("restricted judge returned no selection")
            choice_id = min(scores, key=lambda item: (-scores[item], item))
        elif not isinstance(raw_choice_id, str) or raw_choice_id not in known_ids:
            raise ValueError("restricted judge returned an unknown choice")
        else:
            choice_id = raw_choice_id
        return choice_id, scores


@dataclass(frozen=True)
class GenerativeJudgeRequest:
    region_id: str
    baseline_text: str
    candidate_texts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.region_id or len(self.region_id) > 128:
            raise ValueError("generative judge region ID is invalid")
        if not self.baseline_text or len(self.baseline_text) > 8_000:
            raise ValueError("generative judge baseline is invalid")
        if not self.candidate_texts or len(self.candidate_texts) > 64:
            raise ValueError("generative judge candidates are invalid")
        if any(not text or len(text) > 8_000 for text in self.candidate_texts):
            raise ValueError("generative judge candidate text is invalid")

    def as_payload(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "kind": "local_generative_judge",
            "region_id": self.region_id,
            "baseline_text": self.baseline_text,
            "candidate_texts": list(self.candidate_texts),
        }


@dataclass(frozen=True)
class LocalGenerativeJudgePolicy:
    enabled: bool
    endpoint: str
    allowlisted_endpoints: tuple[str, ...]
    timeout_ms: int = 2_000

    def __post_init__(self) -> None:
        if self.timeout_ms <= 0 or self.timeout_ms > 60_000:
            raise ValueError("generative judge timeout is outside its bounds")


@dataclass(frozen=True)
class GenerativeJudgeOutcome:
    text: str
    status: str
    reason_code: str | None
    execution_path: str = "local_generative"
    restricted_inference_result: bool = False


class LocalJudgeTransport(Protocol):
    def post_json(
        self,
        *,
        endpoint: str,
        payload: Mapping[str, object],
        timeout_ms: int,
        allow_redirects: bool,
    ) -> Mapping[str, object]: ...


class GenerativeResultValidator(Protocol):
    def is_allowed(self, text: str, request: GenerativeJudgeRequest) -> bool: ...


class CandidateOnlyResultValidator:
    """Default provenance guard: a judge may select but not invent text."""

    def is_allowed(self, text: str, request: GenerativeJudgeRequest) -> bool:
        return text in {*request.candidate_texts, request.baseline_text}


class LocalGenerativeJudge:
    """Separate opt-in path for a single allowlisted loopback endpoint."""

    _RESPONSE_KEYS = frozenset({"schema_version", "corrected_text"})

    def __init__(
        self,
        *,
        transport: LocalJudgeTransport,
        validator: GenerativeResultValidator | None = None,
    ) -> None:
        self._transport = transport
        self._validator = validator or CandidateOnlyResultValidator()

    def evaluate(
        self,
        *,
        request: GenerativeJudgeRequest,
        policy: LocalGenerativeJudgePolicy,
    ) -> GenerativeJudgeOutcome:
        if not policy.enabled:
            return _generative_fallback(request, "generative_judge_disabled")
        try:
            endpoint = validate_loopback_endpoint(policy.endpoint, policy.allowlisted_endpoints)
            response = self._transport.post_json(
                endpoint=endpoint,
                payload=request.as_payload(),
                timeout_ms=policy.timeout_ms,
                allow_redirects=False,
            )
            text = self._validate_response(response)
            if not self._validator.is_allowed(text, request):
                return _generative_fallback(request, "generative_output_unprovenanced")
        except Exception:
            return _generative_fallback(request, "generative_judge_failed")
        return GenerativeJudgeOutcome(text=text, status="selected", reason_code=None)

    def _validate_response(self, response: Mapping[str, object]) -> str:
        if not isinstance(response, Mapping) or set(response) - self._RESPONSE_KEYS:
            raise ValueError("generative judge response schema is invalid")
        if response.get("schema_version", "1.0") != "1.0":
            raise ValueError("generative judge schema version is unsupported")
        text = response.get("corrected_text")
        if not isinstance(text, str) or not text or len(text) > 8_000:
            raise ValueError("generative judge corrected text is invalid")
        return text


def validate_loopback_endpoint(endpoint: str, allowlisted_endpoints: tuple[str, ...]) -> str:
    normalized = _normalize_loopback_endpoint(endpoint)
    normalized_allowlist = {_normalize_loopback_endpoint(item) for item in allowlisted_endpoints}
    if normalized not in normalized_allowlist:
        raise ValueError("generative judge endpoint is not allowlisted")
    return normalized


def _normalize_loopback_endpoint(endpoint: str) -> str:
    parsed = urlsplit(str(endpoint))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port is None:
        raise ValueError("generative judge endpoint must be an explicit HTTP loopback endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.startswith("//"):
        raise ValueError("generative judge endpoint contains forbidden URL components")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("generative judge endpoint cannot use DNS") from exc
    if not address.is_loopback or "%" in parsed.hostname:
        raise ValueError("generative judge endpoint is not loopback-local")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme, f"{host}:{parsed.port}", path, "", ""))


def _strict_fallback(request: StrictChoiceRequest, reason_code: str) -> StrictChoiceOutcome:
    choices = {choice.choice_id: choice.text for choice in request.choices}
    return StrictChoiceOutcome(
        choice_id=request.baseline_choice_id,
        text=choices[request.baseline_choice_id],
        status="fallback",
        reason_code=reason_code,
        scores={},
    )


def _generative_fallback(request: GenerativeJudgeRequest, reason_code: str) -> GenerativeJudgeOutcome:
    return GenerativeJudgeOutcome(
        text=request.baseline_text,
        status="fallback",
        reason_code=reason_code,
    )
