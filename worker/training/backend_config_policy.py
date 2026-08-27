"""Fail-closed policy for generated third-party trainer configurations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from worker.training.backends.base import TrainingBackendError

FORBIDDEN_KEYS = frozenset(
    {
        "callback",
        "callbacks",
        "command",
        "deepspeed_config_file",
        "hub_model_id",
        "hub_token",
        "plugin",
        "plugins",
        "run_name",
        "script",
        "trust_remote_code",
        "webui",
    }
)
FORBIDDEN_TEXT_TOKENS = ("$(", "${", "\x00", "\n", "\r", ";", "&&", "||", "`")


class BackendConfigPolicy:
    """Validate only worker-derived values; requests never provide raw config."""

    def __init__(self, *, allowed_roots: tuple[Path, ...]) -> None:
        roots = tuple(root.resolve() for root in allowed_roots)
        if not roots:
            raise ValueError("at least one config path root is required")
        self._roots = roots

    def validate(self, value: Mapping[str, Any]) -> None:
        self._visit(value, path="config")

    def _visit(self, value: Any, *, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str) or key in FORBIDDEN_KEYS or key.startswith("_"):
                    raise TrainingBackendError("config_invalid", f"forbidden generated config field at {path}")
                if key == "push_to_hub" and child is not False:
                    raise TrainingBackendError("config_invalid", "generated config cannot enable Hub upload")
                if key == "report_to" and child not in ("none", [], ()):
                    raise TrainingBackendError("config_invalid", "generated config cannot enable external reporting")
                if key in {
                    "base_model",
                    "checkpoint_dir",
                    "dataset",
                    "model_name_or_path",
                    "output_dir",
                    "path",
                    "project_dir",
                    "resume_from_checkpoint",
                    "valid_path",
                    "validation_path",
                } and isinstance(child, str):
                    self._validate_path(child, f"{path}.{key}")
                self._visit(child, path=f"{path}.{key}")
            return
        if isinstance(value, (list, tuple)):
            if len(value) > 256:
                raise TrainingBackendError("config_invalid", f"generated config list is too large at {path}")
            for index, child in enumerate(value):
                self._visit(child, path=f"{path}[{index}]")
            return
        if value is None or isinstance(value, (bool, int, float)):
            return
        if not isinstance(value, str) or len(value) > 4096:
            raise TrainingBackendError("config_invalid", f"generated config value is invalid at {path}")
        if any(token in value for token in FORBIDDEN_TEXT_TOKENS):
            raise TrainingBackendError("config_invalid", f"generated config contains an unsafe token at {path}")
        if "://" in value:
            raise TrainingBackendError("config_invalid", f"generated config URL is forbidden at {path}")

    def _validate_path(self, value: str, field: str) -> None:
        candidate = Path(value)
        if not candidate.is_absolute():
            raise TrainingBackendError("config_invalid", f"generated path must be absolute at {field}")
        resolved = candidate.resolve(strict=False)
        if not any(resolved == root or root in resolved.parents for root in self._roots):
            raise TrainingBackendError("config_invalid", f"generated path escapes admitted roots at {field}")


__all__ = ["BackendConfigPolicy", "FORBIDDEN_KEYS"]
