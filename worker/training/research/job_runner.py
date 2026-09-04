"""Non-interactive entry point for one Hub-delegated research assignment."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ananta_contracts.research_training import canonical_json
from worker.training.research.assignment_runner import ResearchAssignmentRunner
from worker.training.research.checkpoint import ResearchCheckpointManager
from worker.training.research.distributed import DistributedRuntime
from worker.training.research.preemption import PreemptionController, ResearchStagePreempted
from worker.training.research.real_backend import LocalResearchBackend
from worker.training.research.runtime_verifier import (
    EnvironmentResearchRuntimeVerifier,
    ResearchRuntimeVerifier,
)
from worker.training.research.workspace import ResearchWorkspaceReader


def execute_assignment(
    *,
    assignment_path: Path,
    input_root: Path,
    output_root: Path,
    maximum_input_bytes: int,
    runtime_verifier: ResearchRuntimeVerifier | None = None,
) -> dict[str, Any]:
    try:
        raw_assignment = json.loads(assignment_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research_assignment_file_invalid") from exc
    if not isinstance(raw_assignment, dict):
        raise ValueError("research_assignment_file_invalid")
    world_size = int(dict(raw_assignment.get("run_spec") or {}).get("recipe", {}).get("world_size", 0))
    distributed = DistributedRuntime.ensure(world_size)
    reader = ResearchWorkspaceReader(input_root, maximum_input_bytes=maximum_input_bytes)
    preemption = PreemptionController()
    restore_signals = preemption.install()
    try:
        result = ResearchAssignmentRunner(
            LocalResearchBackend(
                reader,
                checkpoint_manager=ResearchCheckpointManager(
                    output_root / "checkpoints", max_checkpoint_bytes=maximum_input_bytes
                ),
                preemption=preemption,
            ),
            runtime_verifier=runtime_verifier,
        ).execute(raw_assignment)
    except ResearchStagePreempted as exc:
        envelope = {
            "schema": "ananta.research-training-preemption-result.v1",
            "assignment_id": raw_assignment.get("assignment_id"),
            "worker_id": raw_assignment.get("worker_id"),
            "checkpoint": exc.checkpoint_receipt,
            "human_intervention_required": False,
        }
        if distributed.primary:
            _atomic_write(output_root / "preemption.json", canonical_json(envelope).encode())
        distributed.close()
        return envelope
    finally:
        restore_signals()
    content = result.pop("content")
    digest = str(result["manifest"]["artifact_digest"])
    stage_id = str(result["stage_id"])
    relative = Path(stage_id) / f"{digest}.bin"
    target = (output_root.resolve() / relative).resolve()
    if output_root.resolve() not in target.parents:
        raise PermissionError("research_output_path_invalid")
    if distributed.primary:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
    envelope = {
        "schema": "ananta.research-training-worker-result-file.v1",
        "result": result,
        "content_ref": relative.as_posix(),
        "human_intervention_required": False,
    }
    if distributed.primary:
        _atomic_write(output_root / "result.json", canonical_json(envelope).encode())
    distributed.close()
    return envelope


def _atomic_write(target: Path, content: bytes) -> None:
    descriptor, staging = tempfile.mkstemp(prefix=".research-result-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one bounded research-training assignment")
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--maximum-input-bytes", type=int, required=True)
    arguments = parser.parse_args()
    execute_assignment(
        assignment_path=arguments.assignment,
        input_root=arguments.input_root,
        output_root=arguments.output_root,
        maximum_input_bytes=arguments.maximum_input_bytes,
        runtime_verifier=EnvironmentResearchRuntimeVerifier.from_environment(),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through container/subprocess gates
    raise SystemExit(main())


__all__ = ["execute_assignment", "main"]
