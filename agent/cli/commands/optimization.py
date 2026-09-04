"""ananta optimization — headless Hub DSPy optimization controls."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SUBCOMMANDS = ["capabilities", "runs", "dry-run", "create", "cancel", "promote", "provenance", "rollback"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta optimization",
        description="Inspect and control Hub-governed DSPy optimization without interactive approval.",
    )
    sub = parser.add_subparsers(dest="optimization_cmd", metavar="<action>")
    sub.add_parser("capabilities", help="Read the Hub capability projection.")
    runs = sub.add_parser("runs", help="List bounded tenant runs.")
    runs.add_argument("--tenant-id", required=True)
    dry = sub.add_parser("dry-run", help="Validate one spec without a model call.")
    dry.add_argument("--spec", required=True)
    create = sub.add_parser("create", help="Create one policy-admitted run.")
    create.add_argument("--spec", required=True)
    create.add_argument("--idempotency-key", required=True)
    cancel = sub.add_parser("cancel", help="Cancel one run by exact revision.")
    cancel.add_argument("run_id")
    cancel.add_argument("--tenant-id", required=True)
    cancel.add_argument("--revision", required=True, type=int)
    promote = sub.add_parser("promote", help="Apply a digest-bound promotion plan after all automatic gates.")
    promote.add_argument("--plan", required=True)
    promote.add_argument("--evaluation", required=True)
    provenance = sub.add_parser("provenance", help="Read immutable promotion and rollback provenance.")
    provenance.add_argument("--tenant-id", required=True)
    provenance.add_argument("--scope-id", required=True)
    rollback = sub.add_parser("rollback", help="Atomically roll back a promoted scope.")
    rollback.add_argument("--tenant-id", required=True)
    rollback.add_argument("--scope-id", required=True)
    rollback.add_argument("--revision", required=True, type=int)
    return parser


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
        payload = _invoke(parsed)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


def _invoke(parsed: argparse.Namespace) -> Any:
    from agent.cli.api_client import get_api_client

    cmd = parsed.optimization_cmd
    documents: dict[str, dict[str, Any]] = {}
    if cmd in {"dry-run", "create"}:
        documents["spec"] = _document(parsed.spec)
    elif cmd == "promote":
        documents = {"plan": _document(parsed.plan), "evaluation": _document(parsed.evaluation)}
    client = get_api_client()
    if cmd == "capabilities":
        return _data(client.get("/api/dspy-optimization/capabilities"))
    if cmd == "runs":
        return _data(client.get("/api/dspy-optimization/runs", params={"tenant_id": parsed.tenant_id, "limit": 100}))
    if cmd == "dry-run":
        return _data(client.post("/api/dspy-optimization/dry-run", json={"spec": documents["spec"]}))
    if cmd == "create":
        return _data(
            client.post(
                "/api/dspy-optimization/runs",
                json={"spec": documents["spec"], "idempotency_key": parsed.idempotency_key},
            )
        )
    if cmd == "cancel":
        return _data(
            client.post(
                f"/api/dspy-optimization/runs/{parsed.run_id}/cancel",
                json={"tenant_id": parsed.tenant_id, "expected_revision": parsed.revision},
            )
        )
    if cmd == "promote":
        return _data(
            client.post(
                "/api/dspy-optimization/promotion-plans",
                json=documents,
            )
        )
    if cmd == "provenance":
        return _data(
            client.get(
                "/api/dspy-optimization/provenance",
                params={"tenant_id": parsed.tenant_id, "scope_id": parsed.scope_id},
            )
        )
    if cmd == "rollback":
        return _data(
            client.post(
                "/api/dspy-optimization/rollbacks",
                json={
                    "tenant_id": parsed.tenant_id,
                    "scope_id": parsed.scope_id,
                    "expected_revision": parsed.revision,
                },
            )
        )
    raise ValueError("dspy_cli_action_required")


def _document(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, Mapping):
        raise ValueError("dspy_cli_document_invalid")
    return dict(value)


def _data(response: Mapping[str, Any]) -> Any:
    if response.get("status") != "success" or "data" not in response:
        raise ValueError(str(response.get("message") or "dspy_cli_request_failed"))
    return response["data"]


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("optimization", help="Control Hub-governed DSPy optimization.")
    parser.set_defaults(_dispatch=dispatch)


__all__ = ["SUBCOMMANDS", "dispatch", "register"]
