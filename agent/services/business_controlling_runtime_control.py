"""Durable CAS runtime switches for read-only controlling analysis."""

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

_SCHEMA = "ananta.business-controlling-runtime-control.v1"
_ENTRY = re.compile(r"^skillentry_[0-9a-f]{64}$")
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")


class BusinessControllingRuntimeControlError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class BusinessControllingRuntimeControl:
    schema_version: str
    revision: int
    global_enabled: bool
    statistical_enabled: bool
    explanations_enabled: bool
    disabled_catalog_entry_ids: tuple[str, ...]
    actor_id: str
    reason: str
    state_digest: str

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        global_enabled: bool,
        statistical_enabled: bool,
        explanations_enabled: bool,
        disabled_catalog_entry_ids: tuple[str, ...],
        actor_id: str,
        reason: str,
    ) -> "BusinessControllingRuntimeControl":
        normalized = tuple(sorted(disabled_catalog_entry_ids))
        unsigned = {
            "schema_version": _SCHEMA,
            "revision": revision,
            "global_enabled": global_enabled,
            "statistical_enabled": statistical_enabled,
            "explanations_enabled": explanations_enabled,
            "disabled_catalog_entry_ids": list(normalized),
            "actor_id": actor_id,
            "reason": reason,
        }
        state = cls(
            _SCHEMA,
            revision,
            global_enabled,
            statistical_enabled,
            explanations_enabled,
            normalized,
            actor_id,
            reason,
            _digest(unsigned),
        )
        state.validate()
        return state

    @classmethod
    def disabled_default(cls) -> "BusinessControllingRuntimeControl":
        return cls.create(
            revision=0,
            global_enabled=False,
            statistical_enabled=False,
            explanations_enabled=False,
            disabled_catalog_entry_ids=(),
            actor_id="system",
            reason="runtime-control-uninitialized",
        )

    @classmethod
    def from_mapping(cls, value: object) -> "BusinessControllingRuntimeControl":
        fields = {
            "schema_version",
            "revision",
            "global_enabled",
            "statistical_enabled",
            "explanations_enabled",
            "disabled_catalog_entry_ids",
            "actor_id",
            "reason",
            "state_digest",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise BusinessControllingRuntimeControlError(
                "controlling_runtime_control_shape_invalid"
            )
        disabled = value["disabled_catalog_entry_ids"]
        if not isinstance(disabled, list):
            raise BusinessControllingRuntimeControlError(
                "controlling_runtime_control_shape_invalid"
            )
        state = cls(
            value["schema_version"],
            value["revision"],
            value["global_enabled"],
            value["statistical_enabled"],
            value["explanations_enabled"],
            tuple(disabled),
            value["actor_id"],
            value["reason"],
            value["state_digest"],
        )
        state.validate()
        expected = cls.create(
            revision=state.revision,
            global_enabled=state.global_enabled,
            statistical_enabled=state.statistical_enabled,
            explanations_enabled=state.explanations_enabled,
            disabled_catalog_entry_ids=state.disabled_catalog_entry_ids,
            actor_id=state.actor_id,
            reason=state.reason,
        )
        if expected.state_digest != state.state_digest:
            raise BusinessControllingRuntimeControlError(
                "controlling_runtime_control_digest_invalid"
            )
        return state

    def validate(self) -> None:
        if (
            self.schema_version != _SCHEMA
            or isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
            or not all(
                isinstance(value, bool)
                for value in (
                    self.global_enabled,
                    self.statistical_enabled,
                    self.explanations_enabled,
                )
            )
            or tuple(sorted(set(self.disabled_catalog_entry_ids)))
            != self.disabled_catalog_entry_ids
            or any(_ENTRY.fullmatch(value) is None for value in self.disabled_catalog_entry_ids)
            or _ACTOR.fullmatch(self.actor_id) is None
            or not self.reason.strip()
            or len(self.reason) > 500
            or not re.fullmatch(r"[0-9a-f]{64}", self.state_digest)
        ):
            raise BusinessControllingRuntimeControlError(
                "controlling_runtime_control_shape_invalid"
            )

    def catalog_entry_enabled(self, entry_id: str) -> bool:
        return (
            self.global_enabled
            and self.statistical_enabled
            and entry_id not in self.disabled_catalog_entry_ids
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "global_enabled": self.global_enabled,
            "statistical_enabled": self.statistical_enabled,
            "explanations_enabled": self.explanations_enabled,
            "disabled_catalog_entry_ids": list(self.disabled_catalog_entry_ids),
            "actor_id": self.actor_id,
            "reason": self.reason,
            "state_digest": self.state_digest,
        }


class BusinessControllingRuntimeControlRepositoryPort(Protocol):
    def snapshot(self) -> BusinessControllingRuntimeControl: ...

    def compare_and_set(
        self,
        *,
        expected_revision: int,
        replacement: BusinessControllingRuntimeControl,
    ) -> BusinessControllingRuntimeControl: ...


class JsonBusinessControllingRuntimeControlRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_name(f"{path.name}.lock")

    def snapshot(self) -> BusinessControllingRuntimeControl:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._load_unlocked()

    def compare_and_set(
        self,
        *,
        expected_revision: int,
        replacement: BusinessControllingRuntimeControl,
    ) -> BusinessControllingRuntimeControl:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self._load_unlocked()
            if current.revision != expected_revision:
                raise BusinessControllingRuntimeControlError(
                    "controlling_runtime_control_revision_conflict"
                )
            if replacement.revision != current.revision + 1:
                raise BusinessControllingRuntimeControlError(
                    "controlling_runtime_control_revision_invalid"
                )
            self._write_unlocked(replacement)
            return replacement

    def _load_unlocked(self) -> BusinessControllingRuntimeControl:
        if not self._path.exists():
            return BusinessControllingRuntimeControl.disabled_default()
        try:
            return BusinessControllingRuntimeControl.from_mapping(
                json.loads(self._path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise BusinessControllingRuntimeControlError(
                "controlling_runtime_control_unreadable"
            ) from exc

    def _write_unlocked(self, state: BusinessControllingRuntimeControl) -> None:
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
            raise BusinessControllingRuntimeControlError(
                "controlling_runtime_control_write_failed"
            ) from exc


class BusinessControllingRuntimeControlService:
    def __init__(self, repository: BusinessControllingRuntimeControlRepositoryPort) -> None:
        self._repository = repository

    def replace(
        self,
        *,
        expected_revision: int,
        global_enabled: bool,
        statistical_enabled: bool,
        explanations_enabled: bool,
        disabled_catalog_entry_ids: tuple[str, ...],
        actor_id: str,
        reason: str,
    ) -> BusinessControllingRuntimeControl:
        current = self._repository.snapshot()
        replacement = BusinessControllingRuntimeControl.create(
            revision=current.revision + 1,
            global_enabled=global_enabled,
            statistical_enabled=statistical_enabled,
            explanations_enabled=explanations_enabled,
            disabled_catalog_entry_ids=disabled_catalog_entry_ids,
            actor_id=actor_id,
            reason=reason,
        )
        return self._repository.compare_and_set(
            expected_revision=expected_revision,
            replacement=replacement,
        )


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BusinessControllingRuntimeControl",
    "BusinessControllingRuntimeControlError",
    "BusinessControllingRuntimeControlRepositoryPort",
    "BusinessControllingRuntimeControlService",
    "JsonBusinessControllingRuntimeControlRepository",
]
