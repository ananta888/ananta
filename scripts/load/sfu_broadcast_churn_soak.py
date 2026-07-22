#!/usr/bin/env python3
"""SFB-GATE-005 churn, rekey and slow-receiver soak evidence validator."""

from pathlib import Path

try:
    from scripts.sfu_broadcast_advanced_gate import gate_cli
except ModuleNotFoundError:
    from sfu_broadcast_advanced_gate import gate_cli  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    raise SystemExit(
        gate_cli(
            default_profile=ROOT / "config/test-profiles/sfu-broadcast/soak.json",
            default_output=ROOT / "artifacts/test-gates/sfu-broadcast-churn-soak.json",
        )
    )
