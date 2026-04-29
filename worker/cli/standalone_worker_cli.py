from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worker.runtime.standalone_runtime import StandaloneRuntime


class _StaticPolicyPort:
    def classify_command(self, *, command: str, profile: str) -> dict[str, Any]:
        lowered = str(command or "").lower()
        denied = "rm -rf" in lowered or "curl " in lowered and "| sh" in lowered
        return {
            "decision": "deny" if denied else "allow",
            "risk_classification": "critical" if denied else "low",
            "required_approval": denied,
        }


class _ListTracePort:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "payload": dict(payload or {})})


class _ManifestArtifactPort:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []

    def publish(self, *, artifact: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(artifact or {})
        self.artifacts.append(normalized)
        return normalized


def run_cli(*, manifest_path: str, workspace_dir: str) -> dict[str, Any]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    trace_port = _ListTracePort()
    artifact_port = _ManifestArtifactPort()
    runtime = StandaloneRuntime(
        policy_port=_StaticPolicyPort(),
        trace_port=trace_port,
        artifact_port=artifact_port,
    )
    result = runtime.run(task_contract=manifest, workspace_dir=workspace_dir)
    return {
        "result": result,
        "artifacts": artifact_port.artifacts,
        "trace_events": trace_port.events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ananta standalone worker CLI")
    parser.add_argument("--workspace", required=True, help="Workspace root for standalone run")
    parser.add_argument("--manifest", required=True, help="Path to standalone task contract JSON")
    parser.add_argument("--output", required=True, help="Path for machine-readable execution result")
    args = parser.parse_args()
    payload = run_cli(manifest_path=args.manifest, workspace_dir=args.workspace)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

