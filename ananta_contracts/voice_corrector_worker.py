"""Transport-neutral contract for Hub-delegated transcript rewriting."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping, Protocol

CONTRACT_VERSION = "ananta.voice-corrector-worker.v1"
PROMPT_VERSION = "ananta.voice-corrector.prompt.v1"
MAX_TEXT_CHARS = 8_000
MAX_CORRECTED_TEXT_CHARS = 12_000
MAX_EDITS = 2_048
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_MODEL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,191}$")


class VoiceCorrectorContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise VoiceCorrectorContractError("invalid_identifier", f"{field} is invalid")
    return value


def _model_identifier(value: object, *, field: str = "model_id") -> str:
    """Validate a provider model reference without weakening other identifiers.

    Local runtimes commonly expose namespaced IDs such as
    ``team/model:tag``.  Only model fields accept those extra separators;
    request, task and language identifiers retain their narrower contract.
    """

    if not isinstance(value, str) or not _MODEL_IDENTIFIER_RE.fullmatch(value):
        raise VoiceCorrectorContractError("invalid_identifier", f"{field} is invalid")
    return value


def _text(value: object, *, field: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise VoiceCorrectorContractError("invalid_text", f"{field} is invalid")
    normalized = value
    if (not allow_empty and not normalized) or len(normalized) > maximum or "\x00" in normalized:
        raise VoiceCorrectorContractError("invalid_text", f"{field} is invalid")
    return normalized


@dataclass(frozen=True)
class VoiceCorrectorEdit:
    operation: str
    original_start: int
    original_end: int
    original_text: str
    corrected_text: str

    def __post_init__(self) -> None:
        if self.operation not in {"insert", "delete", "replace"}:
            raise VoiceCorrectorContractError("invalid_edit", "edit operation is invalid")
        if (
            isinstance(self.original_start, bool)
            or isinstance(self.original_end, bool)
            or not isinstance(self.original_start, int)
            or not isinstance(self.original_end, int)
            or self.original_start < 0
            or self.original_end < self.original_start
        ):
            raise VoiceCorrectorContractError("invalid_edit", "edit span is invalid")
        _text(self.original_text, field="edit.original_text", maximum=MAX_TEXT_CHARS, allow_empty=True)
        _text(self.corrected_text, field="edit.corrected_text", maximum=MAX_CORRECTED_TEXT_CHARS, allow_empty=True)
        if self.operation == "insert" and (self.original_start != self.original_end or self.original_text):
            raise VoiceCorrectorContractError("invalid_edit", "insert edit is inconsistent")
        if self.operation == "delete" and (not self.original_text or self.corrected_text):
            raise VoiceCorrectorContractError("invalid_edit", "delete edit is inconsistent")
        if self.operation == "replace" and (not self.original_text or not self.corrected_text):
            raise VoiceCorrectorContractError("invalid_edit", "replace edit is inconsistent")

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "original_start": self.original_start,
            "original_end": self.original_end,
            "original_text": self.original_text,
            "corrected_text": self.corrected_text,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "VoiceCorrectorEdit":
        required = {
            "operation",
            "original_start",
            "original_end",
            "original_text",
            "corrected_text",
        }
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise VoiceCorrectorContractError("invalid_edit", "edit envelope is invalid")
        start = raw.get("original_start")
        end = raw.get("original_end")
        if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
            raise VoiceCorrectorContractError("invalid_edit", "edit span is invalid")
        operation = raw.get("operation")
        original_text = raw.get("original_text")
        corrected_text = raw.get("corrected_text")
        if (
            not isinstance(operation, str)
            or not isinstance(original_text, str)
            or not isinstance(corrected_text, str)
        ):
            raise VoiceCorrectorContractError("invalid_edit", "edit values are invalid")
        return cls(
            operation=operation,
            original_start=start,
            original_end=end,
            original_text=original_text,
            corrected_text=corrected_text,
        )


def build_edits(original_text: str, corrected_text: str) -> tuple[VoiceCorrectorEdit, ...]:
    """Create deterministic, replayable character edits for a correction."""

    edits: list[VoiceCorrectorEdit] = []
    for operation, old_start, old_end, new_start, new_end in SequenceMatcher(
        None,
        original_text,
        corrected_text,
        autojunk=False,
    ).get_opcodes():
        if operation == "equal":
            continue
        old_value = original_text[old_start:old_end]
        new_value = corrected_text[new_start:new_end]
        normalized_operation = "insert" if operation == "insert" else "delete" if operation == "delete" else "replace"
        edits.append(
            VoiceCorrectorEdit(
                operation=normalized_operation,
                original_start=old_start,
                original_end=old_end,
                original_text=old_value,
                corrected_text=new_value,
            )
        )
    if len(edits) > MAX_EDITS:
        raise VoiceCorrectorContractError("too_many_edits", "correction contains too many edits")
    return tuple(edits)


def apply_edits(original_text: str, edits: tuple[VoiceCorrectorEdit, ...]) -> str:
    cursor = 0
    corrected: list[str] = []
    for edit in edits:
        if edit.original_start < cursor or edit.original_end > len(original_text):
            raise VoiceCorrectorContractError("invalid_edits", "correction edits overlap or exceed the original")
        if original_text[edit.original_start : edit.original_end] != edit.original_text:
            raise VoiceCorrectorContractError("invalid_edits", "correction edit is not grounded in the original")
        corrected.append(original_text[cursor : edit.original_start])
        corrected.append(edit.corrected_text)
        cursor = edit.original_end
    corrected.append(original_text[cursor:])
    return "".join(corrected)


def edit_ratio(original_text: str, edits: tuple[VoiceCorrectorEdit, ...]) -> float:
    changed = sum(max(len(edit.original_text), len(edit.corrected_text)) for edit in edits)
    return changed / max(1, len(original_text))


@dataclass(frozen=True)
class VoiceCorrectorWorkerRequest:
    request_id: str
    task_id: str
    region_id: str
    original_text: str
    model_id: str
    language: str | None
    max_edit_ratio: float
    deadline_epoch_ms: int

    def __post_init__(self) -> None:
        _identifier(self.request_id, field="request_id")
        _identifier(self.task_id, field="task_id")
        _identifier(self.region_id, field="region_id")
        _model_identifier(self.model_id)
        _text(self.original_text, field="original_text", maximum=MAX_TEXT_CHARS)
        if self.language is not None:
            _identifier(self.language, field="language")
        if (
            isinstance(self.max_edit_ratio, bool)
            or not isinstance(self.max_edit_ratio, (int, float))
            or not math.isfinite(float(self.max_edit_ratio))
            or not 0.01 <= float(self.max_edit_ratio) <= 1.0
        ):
            raise VoiceCorrectorContractError("invalid_edit_ratio", "max_edit_ratio is invalid")
        if (
            isinstance(self.deadline_epoch_ms, bool)
            or not isinstance(self.deadline_epoch_ms, int)
            or self.deadline_epoch_ms <= 0
        ):
            raise VoiceCorrectorContractError("invalid_deadline", "worker deadline is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "region_id": self.region_id,
            "original_text": self.original_text,
            "model_id": self.model_id,
            "language": self.language,
            "max_edit_ratio": self.max_edit_ratio,
            "deadline_epoch_ms": self.deadline_epoch_ms,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "VoiceCorrectorWorkerRequest":
        required = {
            "contract_version",
            "request_id",
            "task_id",
            "region_id",
            "original_text",
            "model_id",
            "language",
            "max_edit_ratio",
            "deadline_epoch_ms",
        }
        if not isinstance(raw, Mapping) or set(raw) != required or raw.get("contract_version") != CONTRACT_VERSION:
            raise VoiceCorrectorContractError("invalid_request", "corrector worker request envelope is invalid")
        deadline = raw.get("deadline_epoch_ms")
        ratio = raw.get("max_edit_ratio")
        if isinstance(deadline, bool) or not isinstance(deadline, int):
            raise VoiceCorrectorContractError("invalid_deadline", "worker deadline is invalid")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise VoiceCorrectorContractError("invalid_edit_ratio", "max_edit_ratio is invalid")
        request_id = _identifier(raw.get("request_id"), field="request_id")
        task_id = _identifier(raw.get("task_id"), field="task_id")
        region_id = _identifier(raw.get("region_id"), field="region_id")
        original_text = _text(raw.get("original_text"), field="original_text", maximum=MAX_TEXT_CHARS)
        model_id = _model_identifier(raw.get("model_id"))
        language_raw = raw.get("language")
        language = (
            _identifier(language_raw, field="language")
            if language_raw is not None
            else None
        )
        return cls(
            request_id=request_id,
            task_id=task_id,
            region_id=region_id,
            original_text=original_text,
            model_id=model_id,
            language=language,
            max_edit_ratio=float(ratio),
            deadline_epoch_ms=deadline,
        )


@dataclass(frozen=True)
class VoiceCorrectorWorkerResponse:
    request_id: str
    task_id: str
    status: str
    original_text: str
    corrected_text: str | None
    edits: tuple[VoiceCorrectorEdit, ...]
    reason_code: str | None
    model_id: str | None
    model_revision: str | None
    engine_id: str | None
    prompt_version: str | None

    def __post_init__(self) -> None:
        _identifier(self.request_id, field="request_id")
        _identifier(self.task_id, field="task_id")
        _text(self.original_text, field="original_text", maximum=MAX_TEXT_CHARS)
        if not isinstance(self.edits, tuple) or len(self.edits) > MAX_EDITS:
            raise VoiceCorrectorContractError(
                "invalid_response",
                "worker response edits are invalid",
            )
        if self.status not in {"corrected", "unchanged", "failed"}:
            raise VoiceCorrectorContractError("invalid_response", "worker response status is invalid")
        if self.status == "failed":
            if self.corrected_text is not None or self.edits or not self.reason_code:
                raise VoiceCorrectorContractError("invalid_response", "failed worker response is invalid")
            _identifier(self.reason_code, field="reason_code")
            if any(
                value is not None
                for value in (self.model_id, self.model_revision, self.engine_id, self.prompt_version)
            ):
                raise VoiceCorrectorContractError("invalid_response", "failed response contains model metadata")
            return
        corrected = _text(
            self.corrected_text,
            field="corrected_text",
            maximum=MAX_CORRECTED_TEXT_CHARS,
        )
        for field, value in (
            ("model_id", self.model_id),
            ("model_revision", self.model_revision),
            ("engine_id", self.engine_id),
            ("prompt_version", self.prompt_version),
        ):
            if field == "model_id":
                _model_identifier(value)
            else:
                _identifier(value, field=field)
        if self.reason_code is not None:
            raise VoiceCorrectorContractError("invalid_response", "completed response cannot contain an error")
        reconstructed = apply_edits(self.original_text, self.edits)
        if reconstructed != corrected:
            raise VoiceCorrectorContractError("invalid_edits", "edits do not reconstruct corrected_text")
        if self.status == "unchanged" and (corrected != self.original_text or self.edits):
            raise VoiceCorrectorContractError("invalid_response", "unchanged response contains a change")
        if self.status == "corrected" and corrected == self.original_text:
            raise VoiceCorrectorContractError("invalid_response", "corrected response contains no change")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "status": self.status,
            "original_text": self.original_text,
            "corrected_text": self.corrected_text,
            "edits": [edit.to_dict() for edit in self.edits],
            "reason_code": self.reason_code,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "engine_id": self.engine_id,
            "prompt_version": self.prompt_version,
            "execution_owner": "worker",
        }

    @classmethod
    def from_dict(cls, raw: object) -> "VoiceCorrectorWorkerResponse":
        required = {
            "contract_version",
            "request_id",
            "task_id",
            "status",
            "original_text",
            "corrected_text",
            "edits",
            "reason_code",
            "model_id",
            "model_revision",
            "engine_id",
            "prompt_version",
            "execution_owner",
        }
        if (
            not isinstance(raw, Mapping)
            or set(raw) != required
            or raw.get("contract_version") != CONTRACT_VERSION
            or raw.get("execution_owner") != "worker"
            or not isinstance(raw.get("edits"), list)
            or len(raw["edits"]) > MAX_EDITS
        ):
            raise VoiceCorrectorContractError("invalid_response", "corrector worker response envelope is invalid")
        request_id = _identifier(raw.get("request_id"), field="request_id")
        task_id = _identifier(raw.get("task_id"), field="task_id")
        status = raw.get("status")
        if not isinstance(status, str):
            raise VoiceCorrectorContractError(
                "invalid_response",
                "worker response status is invalid",
            )
        original_text = _text(
            raw.get("original_text"),
            field="original_text",
            maximum=MAX_TEXT_CHARS,
        )
        corrected_raw = raw.get("corrected_text")
        corrected_text = (
            _text(
                corrected_raw,
                field="corrected_text",
                maximum=MAX_CORRECTED_TEXT_CHARS,
            )
            if corrected_raw is not None
            else None
        )

        def optional_identifier(field: str) -> str | None:
            value = raw.get(field)
            if value is None:
                return None
            return _model_identifier(value) if field == "model_id" else _identifier(value, field=field)

        return cls(
            request_id=request_id,
            task_id=task_id,
            status=status,
            original_text=original_text,
            corrected_text=corrected_text,
            edits=tuple(VoiceCorrectorEdit.from_dict(item) for item in raw["edits"]),
            reason_code=optional_identifier("reason_code"),
            model_id=optional_identifier("model_id"),
            model_revision=optional_identifier("model_revision"),
            engine_id=optional_identifier("engine_id"),
            prompt_version=optional_identifier("prompt_version"),
        )

    def validate_for(self, request: VoiceCorrectorWorkerRequest) -> None:
        if self.request_id != request.request_id or self.task_id != request.task_id:
            raise VoiceCorrectorContractError("correlation_mismatch", "worker response correlation is invalid")
        if self.original_text != request.original_text:
            raise VoiceCorrectorContractError("original_mismatch", "worker did not preserve the original transcript")
        if self.status != "failed":
            if self.model_id != request.model_id:
                raise VoiceCorrectorContractError("model_mismatch", "worker used a different model")
            if edit_ratio(self.original_text, self.edits) > request.max_edit_ratio + 1e-12:
                raise VoiceCorrectorContractError("edit_ratio_exceeded", "correction exceeds its edit-ratio bound")


class VoiceCorrectorWorkerPort(Protocol):
    """Hub-side port; implementations dispatch exactly one bounded worker request."""

    def execute(self, request: VoiceCorrectorWorkerRequest) -> VoiceCorrectorWorkerResponse: ...
