"""ananta dev — developer and CI commands (not for end-users)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SUBCOMMANDS = [
    "acceptance", "e2e", "release-gate", "latency-diagnostics",
    "smoke", "benchmark", "audit", "check", "validate", "evidence",
]

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"

_CHECK_SCRIPTS: dict[str, str] = {
    "pipeline": "check_pipeline.py",
    "cycles": "check_cycles.py",
    "dead-code": "check_dead_code.py",
    "docs": "check_docs_present.py",
    "duplicates": "check_duplicates.py",
    "imports": "check_imports.py",
    "planning-contract": "check_planning_contract.py",
    "policy-and-routing": "check_policy_and_routing.py",
    "service-boundaries": "check_service_boundaries.py",
    "provider-boundaries": "check_core_provider_boundaries.py",
    "hotspot-guardrails": "check_hotspot_guardrails.py",
    "hub-storage": "check_hub_storage.py",
    "security-invariants": "run_security_invariant_checks.py",
}

_AUDIT_SCRIPTS: dict[str, str] = {
    "client-surface": "audit_client_surface_entrypoints.py",
    "domain-integrations": "audit_domain_integrations.py",
    "runtime": "audit_runtime.py",
}

_VALIDATE_SCRIPTS: dict[str, str] = {
    "cross-track-deps": "validate_cross_track_dependencies.py",
    "todo-consistency": "validate_todo_consistency.py",
}

_SMOKE_SCRIPTS: dict[str, str] = {
    "blender": "run_blender_smoke_checks.py",
    "freecad": "run_freecad_smoke_checks.py",
    "client": "smoke_client_golden_paths.py",
    "eclipse": "smoke_eclipse_runtime_bootstrap.py",
    "nvim": "smoke_nvim_runtime.py",
    "tui": "smoke_tui_runtime.py",
}

_BENCHMARK_SCRIPTS: dict[str, str] = {
    "concurrency": "benchmark_concurrency.py",
    "retrieval": "retrieval_benchmark.py",
    "models": "bench_models_live.py",
    "live-click": "run_live_click_dual_benchmark.py",
}

_EVIDENCE_SCRIPTS: dict[str, str] = {
    "real-worker-runtime": "run_real_worker_runtime_evidence.py",
    "core-flow": "run_core_evidence_flow.py",
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta dev",
        description=(
            "Developer and CI commands. Not intended for end-users.\n\n"
            "These commands wrap internal scripts under scripts/ and are\n"
            "intended for CI pipelines, developers, and automated testing."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta dev acceptance --scenario-file scenario_lmstudio.json --sla-seconds 900 --password test123\n"
            "  ananta dev check pipeline\n"
            "  ananta dev check cycles\n"
            "  ananta dev audit client-surface\n"
            "  ananta dev validate todo-consistency\n"
            "  ananta dev smoke blender\n"
            "  ananta dev benchmark concurrency\n"
            "  ananta dev evidence core-flow\n"
            "  ananta dev e2e\n"
            "  ananta dev release-gate\n"
            "  ananta dev latency-diagnostics\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:  # noqa: C901
    sub = p.add_subparsers(dest="dev_cmd", metavar="<action>")

    # acceptance
    acc_p = sub.add_parser(
        "acceptance",
        help="Run first-goal acceptance test (replaces scripts/first_goal_acceptance_runner.py).",
    )
    acc_p.add_argument("--scenario-file", dest="scenario_file", default="scenario_lmstudio.json",
                       help="Path to scenario JSON file.")
    acc_p.add_argument("--sla-seconds", dest="sla_seconds", type=int, default=900,
                       help="SLA timeout in seconds.")
    acc_p.add_argument("--password", default="test123", help="Hub admin password.")
    acc_p.add_argument("--base-url", dest="base_url", default="", help="Hub base URL.")
    acc_p.add_argument("--user", default="admin", help="Hub admin user.")
    acc_p.add_argument("--report-file", dest="report_file", default="",
                       help="Path for JSON report output.")

    # e2e
    sub.add_parser("e2e", help="Run e2e dogfood checks (replaces scripts/run_e2e_dogfood_checks.py).")

    # release-gate
    rg_p = sub.add_parser(
        "release-gate",
        help="Run release gate (replaces scripts/release_gate.py + run_release_gate.py).",
    )
    rg_p.add_argument("--json", action="store_true")

    # latency-diagnostics
    ld_p = sub.add_parser("latency-diagnostics", help="Run goal latency diagnostics.")
    ld_p.add_argument("--goal-id", dest="goal_id", default="", help="Goal ID to analyse.")

    # check
    check_names = sorted(_CHECK_SCRIPTS)
    check_p = sub.add_parser(
        "check",
        help=f"Run a codebase check. Available: {', '.join(check_names)}",
    )
    check_p.add_argument(
        "check_name",
        metavar="<name>",
        choices=check_names,
        help=f"Check to run: {', '.join(check_names)}",
    )
    check_p.add_argument("args", nargs=argparse.REMAINDER, help="Extra args passed to the script.")

    # audit
    audit_names = sorted(_AUDIT_SCRIPTS)
    audit_p = sub.add_parser(
        "audit",
        help=f"Run an audit. Available: {', '.join(audit_names)}",
    )
    audit_p.add_argument(
        "audit_name",
        metavar="<name>",
        choices=audit_names,
        help=f"Audit to run: {', '.join(audit_names)}",
    )
    audit_p.add_argument("args", nargs=argparse.REMAINDER)

    # validate
    validate_names = sorted(_VALIDATE_SCRIPTS)
    val_p = sub.add_parser(
        "validate",
        help=f"Run a validator. Available: {', '.join(validate_names)}",
    )
    val_p.add_argument(
        "validate_name",
        metavar="<name>",
        choices=validate_names,
        help=f"Validator to run: {', '.join(validate_names)}",
    )
    val_p.add_argument("args", nargs=argparse.REMAINDER)

    # smoke
    smoke_names = sorted(_SMOKE_SCRIPTS)
    smoke_p = sub.add_parser(
        "smoke",
        help=f"Run smoke checks. Available: {', '.join(smoke_names)}",
    )
    smoke_p.add_argument(
        "smoke_name",
        metavar="<name>",
        choices=smoke_names,
        help=f"Smoke target: {', '.join(smoke_names)}",
    )
    smoke_p.add_argument("args", nargs=argparse.REMAINDER)

    # benchmark
    bench_names = sorted(_BENCHMARK_SCRIPTS)
    bench_p = sub.add_parser(
        "benchmark",
        help=f"Run a benchmark. Available: {', '.join(bench_names)}",
    )
    bench_p.add_argument(
        "benchmark_name",
        metavar="<name>",
        choices=bench_names,
        help=f"Benchmark to run: {', '.join(bench_names)}",
    )
    bench_p.add_argument("args", nargs=argparse.REMAINDER)

    # evidence
    ev_names = sorted(_EVIDENCE_SCRIPTS)
    ev_p = sub.add_parser(
        "evidence",
        help=f"Run evidence collection. Available: {', '.join(ev_names)}",
    )
    ev_p.add_argument(
        "evidence_name",
        metavar="<name>",
        choices=ev_names,
        help=f"Evidence run: {', '.join(ev_names)}",
    )
    ev_p.add_argument("args", nargs=argparse.REMAINDER)


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.dev_cmd
    if cmd == "acceptance":
        return _cmd_acceptance(parsed)
    if cmd == "e2e":
        return _run_script("run_e2e_dogfood_checks.py", [])
    if cmd == "release-gate":
        return _run_script("run_release_gate.py", ["--json"] if getattr(parsed, "json", False) else [])
    if cmd == "latency-diagnostics":
        extra = ["--goal-id", parsed.goal_id] if getattr(parsed, "goal_id", "") else []
        return _run_script("goal_latency_diagnostics.py", extra)
    if cmd == "check":
        return _run_script(_CHECK_SCRIPTS[parsed.check_name], list(parsed.args or []))
    if cmd == "audit":
        return _run_script(_AUDIT_SCRIPTS[parsed.audit_name], list(parsed.args or []))
    if cmd == "validate":
        return _run_script(_VALIDATE_SCRIPTS[parsed.validate_name], list(parsed.args or []))
    if cmd == "smoke":
        return _run_script(_SMOKE_SCRIPTS[parsed.smoke_name], list(parsed.args or []))
    if cmd == "benchmark":
        return _run_script(_BENCHMARK_SCRIPTS[parsed.benchmark_name], list(parsed.args or []))
    if cmd == "evidence":
        return _run_script(_EVIDENCE_SCRIPTS[parsed.evidence_name], list(parsed.args or []))
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "dev",
        help="Developer and CI commands (not for end-users).",
    )
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_script(script_name: str, extra_args: list[str]) -> int:
    script = _SCRIPTS_DIR / script_name
    if not script.exists():
        print(f"Error: Script not found: {script}", file=sys.stderr)
        print(f"       (looked in {_SCRIPTS_DIR})", file=sys.stderr)
        return 4
    result = subprocess.run(
        [sys.executable, str(script), *extra_args],
    )
    return result.returncode


def _cmd_acceptance(parsed) -> int:
    script = _SCRIPTS_DIR / "first_goal_acceptance_runner.py"
    if not script.exists():
        print(f"Error: Acceptance runner not found: {script}", file=sys.stderr)
        return 4
    args: list[str] = []
    if getattr(parsed, "scenario_file", ""):
        args += ["--scenario-file", parsed.scenario_file]
    if getattr(parsed, "sla_seconds", None) is not None:
        args += ["--sla-seconds", str(parsed.sla_seconds)]
    if getattr(parsed, "password", ""):
        args += ["--password", parsed.password]
    if getattr(parsed, "base_url", ""):
        args += ["--base-url", parsed.base_url]
    if getattr(parsed, "user", ""):
        args += ["--user", parsed.user]
    if getattr(parsed, "report_file", ""):
        args += ["--report-file", parsed.report_file]
    result = subprocess.run([sys.executable, str(script), *args])
    return result.returncode
