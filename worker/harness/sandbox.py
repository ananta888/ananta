"""Execute an admitted offline benchmark in a disposable hardened container."""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from ananta_contracts.harness_genome import canonical_json
from ananta_contracts.harness_sandbox import HarnessSandboxManifest
from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from worker.core.bounded_process import BoundedProcessResult, BoundedProcessRunner


class SandboxProcessPort(Protocol):
    def run(self, command, *, cwd, env, timeout_seconds, max_output_bytes, cancelled) -> BoundedProcessResult: ...


class HarnessContainerSandbox:
    """One closed assignment; no task creation, delegation or evidence issuance.

    Only offline Python/replay benchmarks are admitted. No network, GPU, tools,
    credentials, API invocation or active model gateway is exposed. General LLM
    evaluations require the separate Hub-metered gateway integration.
    """

    def __init__(self, *, runtime: str = "docker", process: SandboxProcessPort | None = None):
        if runtime not in {"docker", "podman"}:
            raise ValueError("harness_sandbox_runtime_invalid")
        self._runtime = runtime
        self._process = process or BoundedProcessRunner()

    def execute(
        self, manifest: HarnessSandboxManifest, assignment: Mapping, *, cancelled: Callable[[], bool] = lambda: False
    ) -> dict:
        manifest.assert_valid()
        projection = validate_hub_evidence_assignment(assignment)
        if cancelled():
            return self._result(manifest, projection, "cancelled")
        # This is a runtime container name, never an evidence identifier.
        name = "ananta-harness-" + uuid.uuid4().hex
        limits = manifest.limits
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        with tempfile.TemporaryDirectory(prefix="ananta-harness-input-") as temporary:
            root = Path(temporary)
            # Only the exact manifest inputs are staged; no repository/host mount.
            root.chmod(0o755)
            (root / "benchmark.py").write_text(manifest.benchmark_source)
            (root / "genome.json").write_text(manifest.genome_json)
            (root / "manifest.json").write_text(canonical_json(manifest.to_mapping()))
            (root / "assignment.json").write_text(canonical_json(projection))
            for path in root.iterdir():
                path.chmod(0o444)
            command = [
                self._runtime,
                "run",
                "--rm",
                "--name",
                name,
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "32",
                "--memory",
                str(limits.memory_bytes),
                "--memory-swap",
                str(limits.memory_bytes),
                "--cpus",
                str(limits.cpus),
                "--ulimit",
                f"cpu={limits.cpu_seconds}:{limits.cpu_seconds}",
                "--ulimit",
                "core=0:0",
                "--log-driver",
                "none",
                "--user",
                "65532:65532",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532",
                "--mount",
                f"type=bind,src={root},dst=/input,readonly",
                "--workdir",
                "/input",
                "--env",
                f"PYTHONHASHSEED={manifest.seed}",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                "python3",
                manifest.image,
                "-B",
                "/input/benchmark.py",
                "/input/genome.json",
            ]
            result = None
            cleanup_ok = False
            try:
                result = self._process.run(
                    command,
                    cwd=root,
                    env=env,
                    timeout_seconds=limits.timeout_seconds,
                    max_output_bytes=limits.max_output_bytes,
                    cancelled=cancelled,
                )
            except (OSError, RuntimeError):
                result = BoundedProcessResult(None, "", "runtime_unavailable")
            finally:
                try:
                    cleanup = self._process.run(
                        [self._runtime, "rm", "--force", name],
                        cwd=root,
                        env=env,
                        timeout_seconds=10,
                        max_output_bytes=4096,
                        cancelled=lambda: False,
                    )
                    # --rm may have already removed it; a successful inspect that
                    # finds it still present must never be accepted as cleanup.
                    if cleanup.returncode == 0 and cleanup.reason == "completed":
                        cleanup_ok = True
                    else:
                        inspect = self._process.run(
                            [self._runtime, "container", "inspect", name],
                            cwd=root,
                            env=env,
                            timeout_seconds=10,
                            max_output_bytes=4096,
                            cancelled=lambda: False,
                        )
                        cleanup_ok = (
                            inspect.returncode == 1 and inspect.reason == "completed" and "No such" in inspect.output
                        )
                except (OSError, RuntimeError):
                    cleanup_ok = False
            if not cleanup_ok:
                return self._result(manifest, projection, "cleanup_failed")
            if result.reason != "completed" or result.returncode != 0:
                reason = result.reason if result.reason != "completed" else "benchmark_failed"
                return self._result(manifest, projection, reason)
            try:
                observation = json.loads(result.output)
                if not isinstance(observation, dict):
                    raise ValueError("observation must be a mapping")
                # Offline-only means no admitted API/model/tool consumption.
                if any(observation.get(key, 0) != 0 for key in ("api_cost", "model_tokens", "tool_calls")):
                    return self._result(manifest, projection, "offline_budget_violation")
                canonical_json(observation)
            except (ValueError, TypeError):
                return self._result(manifest, projection, "benchmark_output_invalid")
            return self._result(manifest, projection, "completed", observation=observation)

    @staticmethod
    def _result(manifest, assignment, reason, *, observation=None):
        return {
            "passed": reason == "completed",
            "reason_code": f"harness_sandbox_{reason}",
            "manifest_digest": manifest.digest,
            "genome_id": manifest.genome.genome_id,
            "suite_id": manifest.suite_id,
            "assignment": assignment,
            "observation": observation,
            "human_intervention_required": False,
        }
