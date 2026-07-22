#!/usr/bin/env python3
"""SFB-GATE-008 10/25/50/100/250 fanout evidence validator."""

from pathlib import Path

try:
    from scripts.sfu_broadcast_advanced_gate import gate_cli
except ModuleNotFoundError:
    from sfu_broadcast_advanced_gate import gate_cli  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    return gate_cli(
        default_profile=ROOT / "config/test-profiles/sfu-broadcast/scale.json",
        default_output=ROOT / "artifacts/test-gates/sfu-broadcast-scale.json",
    )


if __name__ == "__main__":
    raise SystemExit(main())
