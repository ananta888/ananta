#!/usr/bin/env python3
"""Autonomous Snake Chat control E2E runner.

Usage:
    python scripts/e2e/snake_chat_control_e2e.py [--fixture path/to/fixture.json]
    python scripts/e2e/snake_chat_control_e2e.py  # uses default fixture

Exits 0 on success, non-zero on failures.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous Snake Chat E2E runner")
    parser.add_argument(
        "--fixture",
        default="tests/e2e/fixtures/snake_chat_control_basic.json",
        help="Path to E2E fixture JSON",
    )
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"[ERROR] Fixture not found: {fixture_path}", file=sys.stderr)
        return 1

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    mode = fixture.get("mode", "autonomous_e2e")

    os.environ["ANANTA_TUI_CHAT_CONTROL_MODE"] = mode

    try:
        from client_surfaces.operator_tui.chat_control_config import load_chat_control_config
        from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
        from client_surfaces.operator_tui.chat_control_policy import evaluate
        from client_surfaces.operator_tui.tui_action_dispatcher import ActionRequest, TuiActionDispatcher
    except ImportError as exc:
        print(f"[ERROR] Cannot import modules: {exc}", file=sys.stderr)
        return 1

    cfg = load_chat_control_config()
    tui_state = dict(fixture.get("initial_state", {}))
    steps = fixture.get("steps", [])
    passed = 0
    failed = 0

    print(f"=== Snake Chat E2E ({mode}) | {len(steps)} steps | fixture: {fixture_path.name} ===\n")

    for step in steps:
        step_id = step.get("id", "?")
        cmd = step.get("command", "")
        expect_denied = bool(step.get("expect_denied", False))
        expected_marker = step.get("expected_marker") or {}
        expected_state = step.get("expected_state_change") or {}

        parsed = parse_chat_command(cmd)
        decision = evaluate(parsed, config=cfg)

        if not decision.allowed():
            marker = {"status": "denied", "action_id": decision.action_id, "reason": decision.reason}
            if expect_denied:
                print(f"  [{step_id}] PASS (expected deny) — {cmd!r}: {decision.reason}")
                passed += 1
            else:
                print(f"  [{step_id}] FAIL — {cmd!r} denied: {decision.reason}", file=sys.stderr)
                failed += 1
            continue

        dispatcher = TuiActionDispatcher()
        dispatcher.set_tui_state(tui_state)
        req = ActionRequest(action_id=decision.action_id, args=decision.normalized_args, source="e2e")
        result = dispatcher.dispatch(req)

        tui_state.update(result.changed_state_summary)

        marker_ok = _check_marker(result.control_result_marker, expected_marker)
        state_ok = _check_state(tui_state, expected_state)

        if marker_ok and state_ok:
            print(f"  [{step_id}] PASS — {cmd!r}: {result.message}")
            passed += 1
        else:
            details = []
            if not marker_ok:
                details.append(f"marker mismatch: expected {expected_marker}, got {result.control_result_marker}")
            if not state_ok:
                details.append(f"state mismatch: expected {expected_state}")
            print(f"  [{step_id}] FAIL — {cmd!r}: {'; '.join(details)}", file=sys.stderr)
            failed += 1

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


def _check_marker(actual: dict, expected: dict) -> bool:
    return all(actual.get(k) == v for k, v in expected.items())


def _check_state(state: dict, expected: dict) -> bool:
    return all(state.get(k) == v for k, v in expected.items())


if __name__ == "__main__":
    sys.exit(main())
