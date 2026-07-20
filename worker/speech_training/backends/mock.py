"""Deterministic no-model speech training backend used by CI."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

from worker.speech_training.backend import (
    PreparedSpeechTraining,
    SpeechAdapterArtifact,
    SpeechCheckpoint,
    SpeechTrainingBackendError,
    SpeechTrainingContext,
    SpeechTrainingState,
)
from worker.speech_training.contracts import canonical_json, canonical_sha256
from worker.speech_training.evaluation import build_mock_evaluation
from worker.speech_training.process_supervisor import BoundedSpeechChildProcess


class MockSpeechTrainingBackend:
    name = "mock"

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def validate(self, context: SpeechTrainingContext) -> None:
        context.enforce_active()
        if context.dataset.dataset_digest != context.job.dataset.dataset_digest:
            raise SpeechTrainingBackendError("speech_dataset_digest_mismatch", "worker dataset binding changed")
        if context.dataset.split_digest != context.job.dataset.split_digest:
            raise SpeechTrainingBackendError("speech_split_digest_mismatch", "worker split binding changed")
        if context.job.configuration.scenario == "checkpoint_resume" and context.job.resume is None:
            raise SpeechTrainingBackendError(
                "speech_mock_resume_missing",
                "checkpoint-resume scenario requires a complete resume binding",
            )
        if context.job.configuration.scenario == "dataset_only":
            raise SpeechTrainingBackendError("speech_dataset_only", "policy selected dataset-only processing")

    def prepare(self, context: SpeechTrainingContext) -> PreparedSpeechTraining:
        context.enforce_active()
        resume_step = context.job.resume.checkpoint_step if context.job.resume else 0
        preparation_digest = canonical_sha256(
            {
                "binding_digest": context.job.binding_digest,
                "backend": self.name,
                "resume_step": resume_step,
            }
        )
        context.emit("phase", {"phase": "prepared", "step": resume_step})
        return PreparedSpeechTraining(preparation_digest=preparation_digest, resume_step=resume_step)

    def train(
        self,
        context: SpeechTrainingContext,
        prepared: PreparedSpeechTraining,
    ) -> SpeechTrainingState:
        scenario = context.job.configuration.scenario
        last_step = prepared.resume_step
        if scenario == "subprocess_cancel":
            supervisor = BoundedSpeechChildProcess(
                allowed_executables=(Path(sys.executable),),
                termination_grace_seconds=0.1,
            )
            # Ignore graceful termination so the deterministic CI scenario
            # exercises the supervisor's hard-kill escalation.  This process
            # handles no speech/model data and never emits output.
            program = (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(3600)"
            )
            supervisor.run(
                (sys.executable, "-c", program),
                cwd=context.workspace_root,
                abort=context.abort,
                timeout_seconds=min(float(context.job.budget.max_wall_seconds), 60.0),
            )
        for step in range(prepared.resume_step + 1, context.job.configuration.max_steps + 1):
            context.enforce_active()
            if scenario == "cancel" and step == prepared.resume_step + 1:
                context.abort.abort("speech_training_cancelled")
                context.enforce_active()
            if scenario == "deadline" and step == prepared.resume_step + 1:
                raise SpeechTrainingBackendError("speech_deadline_expired", "mock deadline reached")
            if scenario == "lease_lost" and step == prepared.resume_step + 1:
                raise SpeechTrainingBackendError("speech_lease_lost", "mock lease was fenced")
            loss_micros = max(1, 1_000_000 // (step + 1))
            context.emit(
                "progress",
                {
                    "step": step,
                    "max_steps": context.job.configuration.max_steps,
                    "loss_micros": loss_micros,
                },
            )
            last_step = step
        state_digest = canonical_sha256(
            {
                "binding_digest": context.job.binding_digest,
                "completed_steps": last_step,
                "preparation_digest": prepared.preparation_digest,
            }
        )
        return SpeechTrainingState(completed_steps=last_step, state_digest=state_digest)

    def checkpoint(
        self,
        context: SpeechTrainingContext,
        state: SpeechTrainingState,
    ) -> SpeechCheckpoint:
        context.enforce_active()
        content = canonical_json(
            {
                "binding_digest": context.job.binding_digest,
                "state_digest": state.state_digest,
                "step": state.completed_steps,
            }
        )
        path = context.checkpoint_root / "checkpoint.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        context.emit("checkpoint", {"step": state.completed_steps, "sha256": digest})
        return SpeechCheckpoint(step=state.completed_steps, path=path, sha256=digest)

    def evaluate(
        self,
        context: SpeechTrainingContext,
        state: SpeechTrainingState,
    ) -> Mapping[str, Any]:
        del state
        context.enforce_active()
        report = build_mock_evaluation(
            context.job,
            force_failure=context.job.configuration.scenario == "evaluation_fail",
        )
        context.emit("evaluation", {"passed": bool(report["passed"]), "report_digest": canonical_sha256(report)})
        return report

    def export(
        self,
        context: SpeechTrainingContext,
        state: SpeechTrainingState,
        evaluation: Mapping[str, Any],
    ) -> SpeechAdapterArtifact:
        context.enforce_active()
        if context.job.configuration.scenario == "publish_fail":
            raise SpeechTrainingBackendError("speech_mock_publish_failed", "mock artifact export failed")
        content = (
            b"ANANTA-SPEECH-ADAPTER-V1\0"
            + hashlib.sha256(
                canonical_json(
                    {
                        "binding_digest": context.job.binding_digest,
                        "evaluation_digest": canonical_sha256(evaluation),
                        "state_digest": state.state_digest,
                    }
                )
            ).digest()
        )
        path = context.artifact_root / "adapter.speech"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        context.emit("artifact", {"sha256": digest, "size_bytes": len(content)})
        return SpeechAdapterArtifact(path=path, sha256=digest, size_bytes=len(content))

    def cleanup(self, context: SpeechTrainingContext, *, succeeded: bool) -> None:
        temporary = context.workspace_root / "temporary"
        if temporary.exists():
            shutil.rmtree(temporary)
        context.emit("cleanup", {"succeeded": bool(succeeded)})


def fixture_digest(path: Path) -> str:
    """Used by CI to prove a mock manifest contains stable canonical JSON."""

    parsed = json.loads(path.read_text(encoding="utf-8"))
    return canonical_sha256(parsed)
