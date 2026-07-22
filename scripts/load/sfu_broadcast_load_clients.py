#!/usr/bin/env python3
"""Bounded process adapter for protocol-faithful external load clients."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

try:
    from scripts.sfu_broadcast_gate_common import (
        SfuBroadcastGateError,
        read_bounded_json,
        run_bounded_command,
    )
except ModuleNotFoundError:
    from sfu_broadcast_gate_common import (  # type: ignore[no-redef]
        SfuBroadcastGateError,
        read_bounded_json,
        run_bounded_command,
    )


class LoadClientPort(Protocol):
    def execute(self, plan: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ExternalProcessLoadClientAdapter:
    """Runs an explicitly configured client without shell expansion.

    The child receives the canonical plan and an output path through environment
    variables. A zero exit status is not evidence by itself; the gate runner still
    validates the output's real-process and environment attestations.
    """

    command: Sequence[str]
    cwd: Path
    result_path: Path
    timeout_seconds: int
    cpu_seconds_max: int
    memory_bytes_max: int
    artifact_bytes_max: int

    def execute(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.command or self.timeout_seconds < 1:
            raise SfuBroadcastGateError("load_client_adapter_configuration_invalid")
        if self.result_path.is_symlink():
            raise SfuBroadcastGateError("load_client_result_symlink_forbidden")
        self.result_path.unlink(missing_ok=True)
        environment = dict(os.environ)
        environment["ANANTA_SFU_BROADCAST_GATE_PLAN"] = json.dumps(
            plan, sort_keys=True, separators=(",", ":")
        )
        environment["ANANTA_SFU_BROADCAST_RESULT_PATH"] = str(self.result_path)
        command_result = run_bounded_command(
            tuple(self.command),
            cwd=self.cwd,
            env=environment,
            timeout_seconds=self.timeout_seconds,
            cpu_seconds_max=self.cpu_seconds_max,
            memory_bytes_max=self.memory_bytes_max,
        )
        if command_result.timed_out:
            raise SfuBroadcastGateError("load_client_timeout")
        if command_result.exit_code != 0:
            raise SfuBroadcastGateError("load_client_failed")
        return read_bounded_json(self.result_path, maximum_bytes=self.artifact_bytes_max)


__all__ = ["ExternalProcessLoadClientAdapter", "LoadClientPort"]
