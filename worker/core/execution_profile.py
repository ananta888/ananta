from __future__ import annotations

VALID_EXECUTION_PROFILES: tuple[str, ...] = ("safe", "balanced", "fast")
DEFAULT_EXECUTION_PROFILE = "balanced"

_LOOP_BUDGETS = {
    "safe": {"max_iterations": 2, "max_patch_attempts": 2, "max_runtime_seconds": 240},
    "balanced": {"max_iterations": 4, "max_patch_attempts": 4, "max_runtime_seconds": 420},
    "fast": {"max_iterations": 6, "max_patch_attempts": 6, "max_runtime_seconds": 600},
}

_FILE_SELECTION_LIMITS = {
    "safe": {"max_files": 8, "max_bytes": 80_000},
    "balanced": {"max_files": 12, "max_bytes": 120_000},
    "fast": {"max_files": 20, "max_bytes": 220_000},
}

_PROMPT_CONTEXT_CHARS = {
    "safe": 6_000,
    "balanced": 8_000,
    "fast": 12_000,
}


def normalize_execution_profile(value: str | None, *, default: str = DEFAULT_EXECUTION_PROFILE) -> str:
    normalized_default = str(default or DEFAULT_EXECUTION_PROFILE).strip().lower() or DEFAULT_EXECUTION_PROFILE
    if normalized_default not in VALID_EXECUTION_PROFILES:
        normalized_default = DEFAULT_EXECUTION_PROFILE
    normalized = str(value or "").strip().lower()
    if normalized in VALID_EXECUTION_PROFILES:
        return normalized
    return normalized_default


def loop_budgets_for_profile(profile: str | None) -> dict[str, int]:
    normalized = normalize_execution_profile(profile)
    return dict(_LOOP_BUDGETS[normalized])


def file_selection_limits_for_profile(profile: str | None) -> dict[str, int]:
    normalized = normalize_execution_profile(profile)
    return dict(_FILE_SELECTION_LIMITS[normalized])


def prompt_context_chars_for_profile(profile: str | None) -> int:
    normalized = normalize_execution_profile(profile)
    return int(_PROMPT_CONTEXT_CHARS[normalized])
