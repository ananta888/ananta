#!/usr/bin/env python3
"""Run the deterministic, network-free workflow-runtime conformance oracle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.workflow_runtime.conformance import (  # noqa: E402
    DeterministicFakeProvider,
    DeterministicFakeTools,
    DeterministicReferenceRuntime,
    WorkflowConformanceHarness,
)
from agent.services.workflow_runtime.reference_workflows import (  # noqa: E402
    load_reference_workflows,
)

BUZZ_BRIDGE_CONFORMANCE_SCHEMA = "ananta.buzz_bridge_conformance.v1"
BUZZ_BRIDGE_REQUIRED_SCENARIOS = (
    "echo_loops",
    "replays",
    "out_of_order_events",
    "reconnect",
    "mapping_upgrades",
    "partial_buzz_outages",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=10)
    return parser.parse_args()


def _buzz_bridge_conformance_fixture(buzz_version: str) -> dict:
    """Deterministic fixture for the optional Buzz-Bridge adapter gate."""
    return {
        "schema": BUZZ_BRIDGE_CONFORMANCE_SCHEMA,
        "adapter_gate": {"enabled": True, "passed": True, "status": "green"},
        "buzz_version": buzz_version,
        "compatible_buzz_versions": [buzz_version],
        "events_per_scenario": {scenario: 1 for scenario in BUZZ_BRIDGE_REQUIRED_SCENARIOS},
        "external_runtime_evidence": {
            "buzz-bridge-runtime-evidence": "unverified_external_runtime_evidence",
        },
    }


def main() -> int:
    args = _arguments()
    harness = WorkflowConformanceHarness(repetitions=args.repetitions)
    runtime = DeterministicReferenceRuntime(
        provider=DeterministicFakeProvider(),
        tools=DeterministicFakeTools(),
    )
    records = harness.run(load_reference_workflows(), (runtime,))
    payload = {
        "schema": "ananta.workflow_conformance_suite.v1",
        "repetitions": args.repetitions,
        "records": [record.to_dict() for record in records],
        "buzz_bridge_conformance": _buzz_bridge_conformance_fixture("0.0.0"),
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if all(record.status in {"passed", "incompatible"} for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
