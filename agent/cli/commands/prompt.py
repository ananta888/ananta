"""ananta prompt — prompt trace inspection commands (moved from main.py)."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = [
    "inspect", "render", "goal-traces", "goal-report", "delegation-report",
    "task-report", "task-traces", "task-inspect", "task-why",
    "learning-report", "learning-status", "planner-profiles",
    "goal-flows", "goal-stuck", "goal-execmap",
    "artifact-provenance", "goal-artifact-matrix", "goal-worker-traces",
]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta prompt",
        description="Inspect LLM prompt traces, planning outputs, and goal artifacts.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta prompt inspect --trace-id <id>\n"
            "  ananta prompt render --mode generic --goal \"Build a CLI\"\n"
            "  ananta prompt goal-traces --goal-id <id>\n"
            "  ananta prompt goal-report --goal-id <id>\n"
            "  ananta prompt task-report --task-id <id>\n"
            "  ananta prompt task-why --task-id <id>\n"
            "  ananta prompt learning-status\n"
            "  ananta prompt goal-flows --goal-id <id>\n"
            "  ananta prompt artifact-provenance --goal-id <id>\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:  # noqa: C901
    sub = p.add_subparsers(dest="prompt_cmd", metavar="<action>")

    ins_p = sub.add_parser("inspect", help="Show a specific prompt trace by ID.")
    ins_p.add_argument("--trace-id", dest="trace_id", required=True)
    ins_p.add_argument("--json", action="store_true")
    ins_p.add_argument("--raw", action="store_true")
    ins_p.add_argument("--full", action="store_true")

    rnd_p = sub.add_parser("render", help="Render a planning prompt without calling a provider.")
    rnd_p.add_argument("--mode", default="generic")
    rnd_p.add_argument("--goal", default="Test goal")
    rnd_p.add_argument("--language", default="de")
    rnd_p.add_argument("--model-family", dest="model_family")
    rnd_p.add_argument("--context-file", dest="context_file")
    rnd_p.add_argument("--preferred-output-format", dest="preferred_output_format", default="json")
    rnd_p.add_argument("--save-trace", dest="save_trace", action="store_true")
    rnd_p.add_argument("--json", action="store_true")

    gt_p = sub.add_parser("goal-traces", help="Show all prompt traces for a goal.")
    gt_p.add_argument("--goal-id", dest="goal_id", required=True)
    gt_p.add_argument("--json", action="store_true")

    gr_p = sub.add_parser("goal-report", help="Show tasks + prompt traces + artifacts for a goal.")
    gr_p.add_argument("--goal-id", dest="goal_id", required=True)

    dr_p = sub.add_parser("delegation-report", help="Show compact task delegation/template view for a goal.")
    dr_p.add_argument("--goal-id", dest="goal_id", required=True)
    dr_p.add_argument("--json", action="store_true")

    tr_p = sub.add_parser("task-report", help="Show compact prompt/response view for a task.")
    tr_p.add_argument("--task-id", dest="task_id", required=True)
    tr_p.add_argument("--json", action="store_true")

    tt_p = sub.add_parser("task-traces", help="Show all prompt traces for a task.")
    tt_p.add_argument("--task-id", dest="task_id", required=True)
    tt_p.add_argument("--goal-id", dest="goal_id", default="")
    tt_p.add_argument("--propose-only", dest="propose_only", action="store_true")
    tt_p.add_argument("--json", action="store_true")

    ti_p = sub.add_parser("task-inspect", help="Alias for task-report.")
    ti_p.add_argument("--task-id", dest="task_id", required=True)
    ti_p.add_argument("--json", action="store_true")

    lr_p = sub.add_parser("learning-report", help="Show planning learning loop snapshot.")
    lr_p.add_argument("--json", action="store_true")

    ls_p = sub.add_parser("learning-status", help="Show compact planning learning status.")
    ls_p.add_argument("--json", action="store_true")

    pp_p = sub.add_parser("planner-profiles", help="Show planning model profiles.")
    pp_p.add_argument("--provider", default="")
    pp_p.add_argument("--model", default="")
    pp_p.add_argument("--json", action="store_true")

    gf_p = sub.add_parser("goal-flows", help="Compact per-task flow view with executor/propose/artifacts.")
    gf_p.add_argument("--goal-id", dest="goal_id", required=True)
    gf_p.add_argument("--json", action="store_true")

    tw_p = sub.add_parser("task-why", help="Show latest completion/transition reason for a task.")
    tw_p.add_argument("--task-id", dest="task_id", required=True)
    tw_p.add_argument("--json", action="store_true")

    gs_p = sub.add_parser("goal-stuck", help="Show tasks likely stuck in proposing/assigned/in_progress.")
    gs_p.add_argument("--goal-id", dest="goal_id", required=True)
    gs_p.add_argument("--minutes", type=int, default=10)
    gs_p.add_argument("--json", action="store_true")

    ge_p = sub.add_parser("goal-execmap", help="Group tasks by inferred executor.")
    ge_p.add_argument("--goal-id", dest="goal_id", required=True)
    ge_p.add_argument("--json", action="store_true")

    ap_p = sub.add_parser("artifact-provenance", help="Show artifact provenance matrix for a goal.")
    ap_p.add_argument("--goal-id", dest="goal_id", required=True)
    ap_p.add_argument("--json", action="store_true")
    ap_p.add_argument("--out", default="")
    ap_p.add_argument("--with-md", dest="with_md", action="store_true")

    ap_alias = sub.add_parser("goal-artifact-matrix", help="Alias for artifact-provenance.")
    ap_alias.add_argument("--goal-id", dest="goal_id", required=True)
    ap_alias.add_argument("--json", action="store_true")
    ap_alias.add_argument("--out", default="")
    ap_alias.add_argument("--with-md", dest="with_md", action="store_true")

    gwt_p = sub.add_parser("goal-worker-traces", help="Fetch worker-side prompt traces for all tasks in a goal.")
    gwt_p.add_argument("--goal-id", dest="goal_id", required=True)
    gwt_p.add_argument("--propose-only", dest="propose_only", action="store_true")
    gwt_p.add_argument("--full", action="store_true")
    gwt_p.add_argument("--limit", type=int, default=80)
    gwt_p.add_argument("--json", action="store_true")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    from agent.cli.prompt_inspect import run_prompt_command
    return run_prompt_command(parsed)


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "prompt",
        help="Inspect LLM prompt traces and planning outputs.",
    )
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)
