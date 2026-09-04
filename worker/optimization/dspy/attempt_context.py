"""Per-attempt isolation and bounded restart checkpoints for a DSPy worker."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from ananta_contracts.dspy_optimization import canonical_json, require_digest, require_id
from worker.optimization.dspy.cache import DspyRunMemoryCache

_CURRENT: contextvars.ContextVar[DspyAttemptContext | None] = contextvars.ContextVar(
    "ananta_dspy_attempt_context", default=None
)


@dataclass(frozen=True, slots=True)
class DspyAttemptContext:
    tenant_id: str
    run_id: str
    spec_digest: str
    temporary_directory: str
    cache: DspyRunMemoryCache
    checkpoint: Mapping[str, Any] | None


class DspyCheckpointStore:
    def __init__(self, root: str | Path, *, max_bytes: int = 65_536) -> None:
        if not 1_024 <= max_bytes <= 1_048_576:
            raise ValueError("dspy_checkpoint_limit_invalid")
        self._root = Path(root)
        self._max_bytes = max_bytes
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, *, tenant_id: str, run_id: str, spec_digest: str, state: Mapping[str, Any]) -> None:
        path = self._path(tenant_id, run_id)
        value = {
            "schema": "ananta.dspy-checkpoint.v1",
            "tenant_id": require_id(tenant_id, "tenant_id"),
            "run_id": require_id(run_id, "run_id"),
            "spec_digest": require_digest(spec_digest, "spec_digest"),
            "state": dict(state),
        }
        rendered = canonical_json(value).encode()
        if len(rendered) > self._max_bytes:
            raise ValueError("dspy_checkpoint_too_large")
        descriptor, temporary = tempfile.mkstemp(prefix="checkpoint-", suffix=".json", dir=self._root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, *, tenant_id: str, run_id: str, spec_digest: str) -> Mapping[str, Any] | None:
        path = self._path(tenant_id, run_id)
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > self._max_bytes:
            raise ValueError("dspy_checkpoint_invalid")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("dspy_checkpoint_invalid") from exc
        expected = {"schema", "tenant_id", "run_id", "spec_digest", "state"}
        if not isinstance(value, dict) or set(value) != expected or not isinstance(value["state"], dict):
            raise ValueError("dspy_checkpoint_invalid")
        if value["tenant_id"] != tenant_id or value["run_id"] != run_id:
            raise ValueError("dspy_checkpoint_binding_invalid")
        if value["spec_digest"] != spec_digest:
            path.unlink(missing_ok=True)
            return None
        return dict(value["state"])

    def discard(self, *, tenant_id: str, run_id: str) -> None:
        self._path(tenant_id, run_id).unlink(missing_ok=True)

    def _path(self, tenant_id: str, run_id: str) -> Path:
        tenant = require_id(tenant_id, "tenant_id")
        run = require_id(run_id, "run_id")
        identity = "\0".join((tenant, run)).encode()
        return self._root / f"{hashlib.sha256(identity).hexdigest()}.json"


@contextmanager
def isolated_attempt(
    *,
    tenant_id: str,
    run_id: str,
    spec_digest: str,
    workspace_root: str | Path,
    checkpoint: Mapping[str, Any] | None,
) -> Iterator[DspyAttemptContext]:
    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dspy-attempt-", dir=root) as directory:
        context = DspyAttemptContext(
            tenant_id=tenant_id,
            run_id=run_id,
            spec_digest=spec_digest,
            temporary_directory=directory,
            cache=DspyRunMemoryCache(),
            checkpoint=checkpoint,
        )
        token = _CURRENT.set(context)
        try:
            yield context
        finally:
            context.cache.clear()
            _CURRENT.reset(token)


def current_attempt_context() -> DspyAttemptContext:
    value = _CURRENT.get()
    if value is None:
        raise RuntimeError("dspy_attempt_context_unavailable")
    return value


__all__ = ["DspyAttemptContext", "DspyCheckpointStore", "current_attempt_context", "isolated_attempt"]
