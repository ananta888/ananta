"""Persistent CAS kill switches for admitted scientific skills."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_SCHEMA = "ananta.scientific-skill-runtime-control.v1"
_ENTRY_ID = re.compile(r"^skillentry_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")


class ScientificSkillRuntimeControlError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ScientificSkillRuntimeControl:
    schema_version: str
    revision: int
    global_enabled: bool
    disabled_entry_ids: tuple[str, ...]
    actor_id: str
    reason: str
    state_digest: str

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        global_enabled: bool,
        disabled_entry_ids: tuple[str, ...],
        actor_id: str,
        reason: str,
    ) -> "ScientificSkillRuntimeControl":
        normalized = tuple(sorted(disabled_entry_ids))
        payload = {
            "schema_version": _SCHEMA,
            "revision": revision,
            "global_enabled": global_enabled,
            "disabled_entry_ids": list(normalized),
            "actor_id": actor_id,
            "reason": reason,
        }
        state = cls(
            _SCHEMA,
            revision,
            global_enabled,
            normalized,
            actor_id,
            reason,
            _digest(payload),
        )
        state._validate()
        return state

    @classmethod
    def from_mapping(cls, value: object) -> "ScientificSkillRuntimeControl":
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "revision",
            "global_enabled",
            "disabled_entry_ids",
            "actor_id",
            "reason",
            "state_digest",
        }:
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_shape_invalid")
        if not isinstance(value["disabled_entry_ids"], list):
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_shape_invalid")
        state = cls(
            value["schema_version"],
            value["revision"],
            value["global_enabled"],
            tuple(value["disabled_entry_ids"]),
            value["actor_id"],
            value["reason"],
            value["state_digest"],
        )
        state._validate()
        expected = cls.create(
            revision=state.revision,
            global_enabled=state.global_enabled,
            disabled_entry_ids=state.disabled_entry_ids,
            actor_id=state.actor_id,
            reason=state.reason,
        )
        if expected.state_digest != state.state_digest:
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_digest_invalid")
        return state

    @classmethod
    def disabled_default(cls) -> "ScientificSkillRuntimeControl":
        return cls.create(
            revision=0,
            global_enabled=False,
            disabled_entry_ids=(),
            actor_id="system",
            reason="runtime-control-uninitialized",
        )

    def entry_enabled(self, entry_id: str) -> bool:
        return self.global_enabled and entry_id not in self.disabled_entry_ids

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "global_enabled": self.global_enabled,
            "disabled_entry_ids": list(self.disabled_entry_ids),
            "actor_id": self.actor_id,
            "reason": self.reason,
            "state_digest": self.state_digest,
        }

    def _validate(self) -> None:
        if (
            self.schema_version != _SCHEMA
            or isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
            or not isinstance(self.global_enabled, bool)
            or len(set(self.disabled_entry_ids)) != len(self.disabled_entry_ids)
            or tuple(sorted(self.disabled_entry_ids)) != self.disabled_entry_ids
            or any(
                not isinstance(value, str) or _ENTRY_ID.fullmatch(value) is None
                for value in self.disabled_entry_ids
            )
            or not isinstance(self.actor_id, str)
            or _ACTOR.fullmatch(self.actor_id) is None
            or not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > 500
            or not isinstance(self.state_digest, str)
            or _DIGEST.fullmatch(self.state_digest) is None
        ):
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_shape_invalid")


class ScientificSkillRuntimeControlRepositoryPort(Protocol):
    def snapshot(self) -> ScientificSkillRuntimeControl: ...

    def compare_and_set(
        self,
        *,
        expected_revision: int,
        replacement: ScientificSkillRuntimeControl,
    ) -> ScientificSkillRuntimeControl: ...


class JsonScientificSkillRuntimeControlRepository:
    """Hub-local durable state with process-safe CAS and atomic replacement."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_name(f"{path.name}.lock")

    def snapshot(self) -> ScientificSkillRuntimeControl:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._load_unlocked()

    def compare_and_set(
        self,
        *,
        expected_revision: int,
        replacement: ScientificSkillRuntimeControl,
    ) -> ScientificSkillRuntimeControl:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self._load_unlocked()
            if current.revision != expected_revision:
                raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_revision_conflict")
            if replacement.revision != current.revision + 1:
                raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_revision_invalid")
            self._write_unlocked(replacement)
            return replacement

    def _load_unlocked(self) -> ScientificSkillRuntimeControl:
        if not self._path.exists():
            return ScientificSkillRuntimeControl.disabled_default()
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_unreadable") from exc
        return ScientificSkillRuntimeControl.from_mapping(value)

    def _write_unlocked(self, state: ScientificSkillRuntimeControl) -> None:
        encoded = json.dumps(state.to_mapping(), sort_keys=True, indent=2) + "\n"
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
        except OSError as exc:
            if temporary_path is not None:
                Path(temporary_path).unlink(missing_ok=True)
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_write_failed") from exc


class ScientificSkillRuntimeControlService:
    def __init__(self, repository: ScientificSkillRuntimeControlRepositoryPort) -> None:
        self._repository = repository

    def set_global(
        self,
        *,
        enabled: bool,
        expected_revision: int,
        actor_id: str,
        reason: str,
    ) -> ScientificSkillRuntimeControl:
        current = self._repository.snapshot()
        replacement = ScientificSkillRuntimeControl.create(
            revision=current.revision + 1,
            global_enabled=enabled,
            disabled_entry_ids=current.disabled_entry_ids,
            actor_id=actor_id,
            reason=reason,
        )
        return self._repository.compare_and_set(
            expected_revision=expected_revision,
            replacement=replacement,
        )

    def set_entry(
        self,
        *,
        entry_id: str,
        enabled: bool,
        expected_revision: int,
        actor_id: str,
        reason: str,
    ) -> ScientificSkillRuntimeControl:
        if _ENTRY_ID.fullmatch(entry_id) is None:
            raise ScientificSkillRuntimeControlError("scientific_skill_runtime_control_entry_invalid")
        current = self._repository.snapshot()
        disabled = set(current.disabled_entry_ids)
        if enabled:
            disabled.discard(entry_id)
        else:
            disabled.add(entry_id)
        replacement = ScientificSkillRuntimeControl.create(
            revision=current.revision + 1,
            global_enabled=current.global_enabled,
            disabled_entry_ids=tuple(disabled),
            actor_id=actor_id,
            reason=reason,
        )
        return self._repository.compare_and_set(
            expected_revision=expected_revision,
            replacement=replacement,
        )


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "JsonScientificSkillRuntimeControlRepository",
    "ScientificSkillRuntimeControl",
    "ScientificSkillRuntimeControlError",
    "ScientificSkillRuntimeControlRepositoryPort",
    "ScientificSkillRuntimeControlService",
]
