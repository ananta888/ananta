"""Backend routing priority: the per-task-kind map wins over the default.

`resolve_cli_backend` reads `sgpt_routing.task_kind_backend[task_kind]` before
`sgpt_routing.default_backend`. A worker that only overrides `default_backend`
therefore keeps running `coding` tasks on the mapped backend — the regression
that made an `opencode` worker execute the `ananta-worker` loop.
"""
from __future__ import annotations

from agent.runtime_policy import resolve_cli_backend

SUPPORTED = {"sgpt", "ananta-worker", "opencode", "codex", "aider"}


def _cfg(**routing) -> dict:
    return {"sgpt_routing": routing}


def test_task_kind_map_wins_over_default_backend():
    cfg = _cfg(
        default_backend="opencode",
        task_kind_backend={"coding": "ananta-worker", "research": "deerflow"},
    )
    backend, reason, _ = resolve_cli_backend(
        task_kind="coding",
        requested_backend="auto",
        supported_backends=SUPPORTED,
        agent_cfg=cfg,
        fallback_backend="sgpt",
    )
    assert backend == "ananta-worker"
    assert reason.startswith("task_kind_policy:coding")


def test_default_backend_governs_unmapped_kinds():
    cfg = _cfg(
        default_backend="opencode",
        task_kind_backend={"research": "deerflow"},
    )
    backend, reason, _ = resolve_cli_backend(
        task_kind="coding",
        requested_backend="auto",
        supported_backends=SUPPORTED,
        agent_cfg=cfg,
        fallback_backend="sgpt",
    )
    assert backend == "opencode"
    assert reason.startswith("default_policy:opencode")


def test_env_style_map_makes_a_kind_follow_the_worker_backend():
    cfg = _cfg(
        default_backend="opencode",
        task_kind_backend={"coding": "opencode", "research": "deerflow"},
    )
    for kind in ("coding", "analysis", "doc", "ops"):
        backend, _, _ = resolve_cli_backend(
            task_kind=kind,
            requested_backend="auto",
            supported_backends=SUPPORTED,
            agent_cfg=cfg,
            fallback_backend="sgpt",
        )
        assert backend == "opencode", f"{kind} must follow the worker backend"


def test_explicit_request_still_outranks_both():
    cfg = _cfg(default_backend="opencode", task_kind_backend={"coding": "ananta-worker"})
    backend, reason, _ = resolve_cli_backend(
        task_kind="coding",
        requested_backend="sgpt",
        supported_backends=SUPPORTED,
        agent_cfg=cfg,
        fallback_backend="sgpt",
    )
    assert backend == "sgpt"
    assert reason == "explicit_backend:sgpt"


def test_unsupported_kind_mapping_falls_back_to_default():
    cfg = _cfg(default_backend="opencode", task_kind_backend={"coding": "not-a-backend"})
    backend, _, _ = resolve_cli_backend(
        task_kind="coding",
        requested_backend="auto",
        supported_backends=SUPPORTED,
        agent_cfg=cfg,
        fallback_backend="sgpt",
    )
    assert backend == "opencode"
