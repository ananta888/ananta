#!/usr/bin/env python3
"""SFB-GATE-006 multi-room fleet and split-brain evidence validator."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_advanced_gate import gate_cli  # noqa: E402,I001


if __name__ == "__main__":
    raise SystemExit(
        gate_cli(
            default_profile=ROOT / "config/test-profiles/sfu-broadcast/fleet-chaos.json",
            default_output=ROOT / "artifacts/test-gates/sfu-broadcast-fleet-chaos.json",
        )
    )
