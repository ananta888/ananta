"""Transport-neutral contract for Hub-delegated generative candidate judging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol

CONTRACT_VERSION = "ananta.generative-judge-worker.v1"
MAX_CANDIDATES = 64
MAX_TEXT_CHARS = 8_000
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class GenerativeJudgeContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise GenerativeJudgeContractError("invalid_identifier", f"{field} is invalid")
    return normalized


@dataclass(frozen=True)
class GenerativeJudgeCandidate:
    choice_id: str
    text: str

    def __post_init__(self) -> None:
        _identifier(self.choice_id, field="choice_id")
        if not self.text or len(self.text) > MAX_TEXT_CHARS or "\x00" in self.text:
            raise GenerativeJudgeContractError("invalid_candidate", "candidate text is invalid")

    def to_dict(self) -> dict[str, str]:
        return {"choice_id": self.choice_id, "text": self.text}

    @classmethod
    def from_dict(cls, raw: object) -> "GenerativeJudgeCandidate":
        if not isinstance(raw, Mapping) or set(raw) != {"choice_id", "text"}:
            raise GenerativeJudgeContractError("invalid_candidate", "candidate envelope is invalid")
        return cls(choice_id=str(raw["choice_id"]), text=str(raw["text"]))


@dataclass(frozen=True)
class GenerativeJudgeWorkerRequest:
    request_id: str
    task_id: str
    region_id: str
    candidates: tuple[GenerativeJudgeCandidate, ...]
    baseline_choice_id: str
    deadline_epoch_ms: int

    def __post_init__(self) -> None:
        _identifier(self.request_id, field="request_id")
        _identifier(self.task_id, field="task_id")
        _identifier(self.region_id, field="region_id")
        if not 1 <= len(self.candidates) <= MAX_CANDIDATES:
            raise GenerativeJudgeContractError("invalid_candidates", "candidate count is outside its bounds")
        choice_ids = tuple(candidate.choice_id for candidate in self.candidates)
        if len(set(choice_ids)) != len(choice_ids):
            raise GenerativeJudgeContractError("invalid_candidates", "candidate IDs must be unique")
        if self.baseline_choice_id not in choice_ids:
            raise GenerativeJudgeContractError("invalid_baseline", "baseline candidate is unknown")
        if isinstance(self.deadline_epoch_ms, bool) or self.deadline_epoch_ms <= 0:
            raise GenerativeJudgeContractError("invalid_deadline", "worker deadline is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "region_id": self.region_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "baseline_choice_id": self.baseline_choice_id,
            "deadline_epoch_ms": self.deadline_epoch_ms,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "GenerativeJudgeWorkerRequest":
        required = {
            "contract_version",
            "request_id",
            "task_id",
            "region_id",
            "candidates",
            "baseline_choice_id",
            "deadline_epoch_ms",
        }
        if not isinstance(raw, Mapping) or set(raw) != required or raw.get("contract_version") != CONTRACT_VERSION:
            raise GenerativeJudgeContractError("invalid_request", "judge worker request envelope is invalid")
        raw_candidates = raw.get("candidates")
        if not isinstance(raw_candidates, list):
            raise GenerativeJudgeContractError("invalid_candidates", "candidate list is invalid")
        deadline = raw.get("deadline_epoch_ms")
        if isinstance(deadline, bool) or not isinstance(deadline, int):
            raise GenerativeJudgeContractError("invalid_deadline", "worker deadline is invalid")
        return cls(
            request_id=str(raw["request_id"]),
            task_id=str(raw["task_id"]),
            region_id=str(raw["region_id"]),
            candidates=tuple(GenerativeJudgeCandidate.from_dict(item) for item in raw_candidates),
            baseline_choice_id=str(raw["baseline_choice_id"]),
            deadline_epoch_ms=deadline,
        )


@dataclass(frozen=True)
class GenerativeJudgeWorkerResponse:
    request_id: str
    task_id: str
    status: str
    choice_id: str | None
    reason_code: str | None
    engine_id: str | None

    def __post_init__(self) -> None:
        _identifier(self.request_id, field="request_id")
        _identifier(self.task_id, field="task_id")
        if self.status not in {"selected", "failed"}:
            raise GenerativeJudgeContractError("invalid_response", "worker response status is invalid")
        if self.status == "selected":
            _identifier(self.choice_id, field="choice_id")
            _identifier(self.engine_id, field="engine_id")
            if self.reason_code is not None:
                raise GenerativeJudgeContractError("invalid_response", "selected response cannot contain an error")
        elif self.choice_id is not None or self.engine_id is not None or not self.reason_code:
            raise GenerativeJudgeContractError("invalid_response", "failed response is invalid")
        else:
            _identifier(self.reason_code, field="reason_code")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "status": self.status,
            "choice_id": self.choice_id,
            "reason_code": self.reason_code,
            "engine_id": self.engine_id,
            "execution_owner": "worker",
        }

    @classmethod
    def from_dict(cls, raw: object) -> "GenerativeJudgeWorkerResponse":
        required = {
            "contract_version",
            "request_id",
            "task_id",
            "status",
            "choice_id",
            "reason_code",
            "engine_id",
            "execution_owner",
        }
        if (
            not isinstance(raw, Mapping)
            or set(raw) != required
            or raw.get("contract_version") != CONTRACT_VERSION
            or raw.get("execution_owner") != "worker"
        ):
            raise GenerativeJudgeContractError("invalid_response", "judge worker response envelope is invalid")
        return cls(
            request_id=str(raw["request_id"]),
            task_id=str(raw["task_id"]),
            status=str(raw["status"]),
            choice_id=str(raw["choice_id"]) if raw["choice_id"] is not None else None,
            reason_code=str(raw["reason_code"]) if raw["reason_code"] is not None else None,
            engine_id=str(raw["engine_id"]) if raw["engine_id"] is not None else None,
        )

    def validate_for(self, request: GenerativeJudgeWorkerRequest) -> None:
        if self.request_id != request.request_id or self.task_id != request.task_id:
            raise GenerativeJudgeContractError("correlation_mismatch", "worker response correlation is invalid")
        if self.status == "selected" and self.choice_id not in {
            candidate.choice_id for candidate in request.candidates
        }:
            raise GenerativeJudgeContractError("unprovenanced_choice", "worker selected an unknown candidate")


class GenerativeJudgeWorkerPort(Protocol):
    """Hub-side port; implementations may use HTTP or another bounded transport."""

    def execute(self, request: GenerativeJudgeWorkerRequest) -> GenerativeJudgeWorkerResponse: ...
