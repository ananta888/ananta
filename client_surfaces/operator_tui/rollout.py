from __future__ import annotations

import os


ROLLOUT_STAGES = ("local_dev", "advanced_opt_in", "default_candidate", "default")


def operator_tui_enabled(env: dict[str, str] | None = None) -> bool:
    values = env or os.environ
    flag = values.get("ANANTA_OPERATOR_TUI_ENABLED", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def rollout_stage(env: dict[str, str] | None = None) -> str:
    values = env or os.environ
    candidate = values.get("ANANTA_OPERATOR_TUI_STAGE", "local_dev").strip().lower()
    return candidate if candidate in ROLLOUT_STAGES else "local_dev"


def rollback_hint() -> str:
    return "Use legacy `ananta tui` or set ANANTA_OPERATOR_TUI_ENABLED=0 to disable the operator TUI path."
