#!/usr/bin/env python3
"""WebRTC ICE/STUN/TURN smoke probe for Ananta operator TUI.

Usage
-----
  # Mock mode (no network, always succeeds):
  python scripts/operator_tui_webrtc_smoke.py --mock

  # Probe a STUN server:
  python scripts/operator_tui_webrtc_smoke.py --stun stun:webrtc.ananta.de:3478

  # Probe a TURN server:
  python scripts/operator_tui_webrtc_smoke.py --turn turn:webrtc.ananta.de:3478 \\
      --turn-user ananta --turn-cred secret

  # Probe both:
  python scripts/operator_tui_webrtc_smoke.py \\
      --stun stun:webrtc.ananta.de:3478 \\
      --turn turn:webrtc.ananta.de:3478 --turn-user ananta --turn-cred secret

Output is JSON to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
import time


def _mock_result() -> dict:
    return {
        "mode": "mock",
        "timestamp": time.time(),
        "stun": {
            "stun_reachable": True,
            "turn_reachable": False,
            "candidate_types": ["host", "srflx"],
            "error": "",
            "duration_ms": 1.0,
        },
        "turn": {
            "stun_reachable": True,
            "turn_reachable": True,
            "candidate_types": ["host", "srflx", "relay"],
            "error": "",
            "duration_ms": 2.0,
        },
        "summary": "mock: all probes succeeded",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ananta WebRTC ICE/STUN/TURN smoke probe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Return fake successful probe result without network access",
    )
    parser.add_argument(
        "--stun",
        metavar="URL",
        default="",
        help="STUN server URL, e.g. stun:webrtc.ananta.de:3478",
    )
    parser.add_argument(
        "--turn",
        metavar="URL",
        default="",
        help="TURN server URL, e.g. turn:webrtc.ananta.de:3478",
    )
    parser.add_argument(
        "--turn-user",
        metavar="USERNAME",
        default="",
        help="TURN username",
    )
    parser.add_argument(
        "--turn-cred",
        metavar="CREDENTIAL",
        default="",
        help="TURN credential",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Probe timeout in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent level (default: 2)",
    )

    args = parser.parse_args()

    if args.mock:
        print(json.dumps(_mock_result(), indent=args.indent))
        return 0

    # Real probes
    try:
        import sys as _sys
        import os as _os
        # Add repo root to path if running as a script
        repo_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if repo_root not in _sys.path:
            _sys.path.insert(0, repo_root)
        from client_surfaces.operator_tui.realtime.ice_probe import IceProbe
    except ImportError as exc:
        print(
            json.dumps({"error": f"Cannot import IceProbe: {exc}", "mode": "error"}),
            indent=args.indent,
        )
        return 1

    probe = IceProbe()
    result: dict = {
        "mode": "live",
        "timestamp": time.time(),
        "stun": None,
        "turn": None,
        "summary": "",
    }

    if args.stun:
        r = probe.probe_stun(args.stun, timeout=args.timeout)
        result["stun"] = {
            "stun_reachable": r.stun_reachable,
            "turn_reachable": r.turn_reachable,
            "candidate_types": r.candidate_types,
            "error": r.error,
            "duration_ms": r.duration_ms,
        }

    if args.turn:
        if not args.turn_user:
            result["turn"] = {"error": "--turn-user required for TURN probe"}
        else:
            r = probe.probe_turn(
                args.turn,
                username=args.turn_user,
                credential=args.turn_cred,
                timeout=args.timeout,
            )
            result["turn"] = {
                "stun_reachable": r.stun_reachable,
                "turn_reachable": r.turn_reachable,
                "candidate_types": r.candidate_types,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }

    if not args.stun and not args.turn:
        result["summary"] = "No --stun or --turn specified. Use --mock for a dry run."
    else:
        errors = []
        if result["stun"] and result["stun"].get("error"):
            errors.append(f"STUN: {result['stun']['error']}")
        if result["turn"] and result["turn"].get("error"):
            errors.append(f"TURN: {result['turn']['error']}")
        result["summary"] = "errors: " + "; ".join(errors) if errors else "all probes passed"

    print(json.dumps(result, indent=args.indent))
    return 0 if not result.get("summary", "").startswith("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
