#!/usr/bin/env python3
"""APRL-021: Validate agent profile map against the actual file system.

Checks:
  - profile-map.json syntax is valid JSON
  - every agents_file in the map exists inside the repo
  - every docs/agent-profiles/<id>/AGENTS.md has a corresponding map entry
  - all required schema fields are present per profile

Exit code 0 = all OK, 1 = validation errors found.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_MAP_PATH = REPO_ROOT / "docs" / "agent-profiles" / "profile-map.json"
PROFILE_DIR = REPO_ROOT / "docs" / "agent-profiles"
REQUIRED_FIELDS = {"activation", "agents_file", "primary_role"}
OPTIONAL_FIELDS = {"allowed_task_kinds", "code_change_policy", "context_policy_hint"}


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    # --- 1. JSON syntax ---
    if not PROFILE_MAP_PATH.exists():
        errors.append(f"profile-map.json not found: {_rel(PROFILE_MAP_PATH)}")
        _report(errors, warnings)
        return 1

    try:
        profile_map = json.loads(PROFILE_MAP_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"profile-map.json JSON syntax error: {exc}")
        _report(errors, warnings)
        return 1

    profiles: dict = dict(profile_map.get("profiles") or {})

    # --- 2. Required fields + agents_file existence ---
    mapped_agent_files: set[str] = set()
    for profile_id, cfg in profiles.items():
        missing = REQUIRED_FIELDS - set(cfg.keys())
        if missing:
            errors.append(f"profile '{profile_id}' missing required fields: {sorted(missing)}")

        agents_file_rel = str(cfg.get("agents_file") or "").strip()
        if not agents_file_rel:
            errors.append(f"profile '{profile_id}' has empty agents_file")
            continue

        # Path traversal guard
        try:
            resolved = (REPO_ROOT / agents_file_rel).resolve()
            if not str(resolved).startswith(str(REPO_ROOT)):
                errors.append(
                    f"profile '{profile_id}' agents_file escapes repo root: {agents_file_rel}"
                )
                continue
        except Exception as exc:
            errors.append(f"profile '{profile_id}' agents_file resolve error: {exc}")
            continue

        if not resolved.exists():
            errors.append(
                f"profile '{profile_id}' agents_file not found: {agents_file_rel}"
            )
        else:
            mapped_agent_files.add(agents_file_rel)

        # Warn about missing optional fields
        missing_optional = OPTIONAL_FIELDS - set(cfg.keys())
        if missing_optional:
            warnings.append(
                f"profile '{profile_id}' missing optional fields: {sorted(missing_optional)}"
            )

        # Warn about empty activation list
        activation = list(cfg.get("activation") or [])
        if not activation:
            warnings.append(f"profile '{profile_id}' has empty activation list")

    # --- 3. Orphaned docs/agent-profiles/<id>/AGENTS.md ---
    for agents_md in PROFILE_DIR.glob("*/AGENTS.md"):
        rel = str(agents_md.relative_to(REPO_ROOT)).replace("\\", "/")
        if rel not in mapped_agent_files:
            warnings.append(
                f"orphaned AGENTS.md without profile-map entry: {rel}"
            )

    # --- 4. global_profile file check ---
    global_profile_rel = str(profile_map.get("global_profile") or "").strip()
    if global_profile_rel:
        global_path = REPO_ROOT / global_profile_rel
        if not global_path.exists():
            errors.append(f"global_profile not found: {global_profile_rel}")

    _report(errors, warnings)
    return 1 if errors else 0


def _report(errors: list[str], warnings: list[str]) -> None:
    if warnings:
        print(f"WARNINGS ({len(warnings)}):", file=sys.stderr)
        for w in warnings:
            print(f"  [WARN] {w}", file=sys.stderr)
    if errors:
        print(f"\nERRORS ({len(errors)}):", file=sys.stderr)
        for e in errors:
            print(f"  [ERROR] {e}", file=sys.stderr)
        print("\nValidation FAILED.", file=sys.stderr)
    else:
        print(
            f"agent profile validation OK — {len(warnings)} warning(s), 0 errors.",
            file=sys.stdout,
        )


if __name__ == "__main__":
    sys.exit(main())
