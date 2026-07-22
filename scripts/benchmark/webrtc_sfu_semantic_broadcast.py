#!/usr/bin/env python3
"""Semantic-broadcast entry point for the shared SFB-GATE-008 matrix."""

try:
    from scripts.benchmark.webrtc_sfu_broadcast_fanout import main
except ModuleNotFoundError:
    from webrtc_sfu_broadcast_fanout import main  # type: ignore[no-redef]


if __name__ == "__main__":
    raise SystemExit(main())
