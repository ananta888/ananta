from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_data import (
    LEARNED_PATTERN_SCHEMA_FILE,
    PROFILE_SCHEMA_FILE,
    TRAINING_BUNDLE_SCHEMA_FILE,
    validate_learned_pattern,
    validate_payload,
    validate_prediction_profile,
)
from client_surfaces.operator_tui.ai_snake_training_store import build_training_bundle


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-Snake training data CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate bundle/profile/pattern payload.")
    validate.add_argument("path")

    export = sub.add_parser("export", help="Export training bundle as JSON.")
    export.add_argument("--stdout", action="store_true")
    export.add_argument("--include-events", action="store_true")

    summarize = sub.add_parser("summarize", help="Print short summary for a JSON payload.")
    summarize.add_argument("path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "validate":
        payload = _read_json(args.path)
        errors = _validate_any_payload(payload)
        if errors:
            print("invalid")
            for err in errors:
                print(f"- {err}")
            return 2
        print("valid")
        return 0

    if args.command == "export":
        if not args.stdout:
            print("export requires --stdout")
            return 2
        payload = build_training_bundle(include_events=bool(args.include_events))
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.command == "summarize":
        payload = _read_json(args.path)
        summary = _summarize_payload(payload)
        print(summary)
        return 0
    return 2


def _read_json(path: str) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("json root must be object")
    return payload


def _validate_any_payload(payload: dict[str, Any]) -> list[str]:
    schema_version = str(payload.get("schema_version") or "")
    if schema_version == "ai_snake_training_bundle.v1":
        return validate_payload(payload, schema_filename=TRAINING_BUNDLE_SCHEMA_FILE)
    if schema_version == "ai_snake_prediction_profile.v1":
        return validate_prediction_profile(payload)
    if schema_version == "ai_snake_learned_pattern.v1":
        return validate_learned_pattern(payload)
    if schema_version == "ai_snake_learned_pattern.v1-list":
        patterns = payload.get("patterns")
        if not isinstance(patterns, list):
            return ["patterns must be list"]
        errors: list[str] = []
        for idx, item in enumerate(patterns):
            if not isinstance(item, dict):
                errors.append(f"patterns[{idx}] is not object")
                continue
            for err in validate_payload(item, schema_filename=LEARNED_PATTERN_SCHEMA_FILE):
                errors.append(f"patterns[{idx}].{err}")
        return errors
    return ["unsupported schema_version"]


def _summarize_payload(payload: dict[str, Any]) -> str:
    schema_version = str(payload.get("schema_version") or "unknown")
    if schema_version == "ai_snake_training_bundle.v1":
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        patterns = payload.get("patterns") if isinstance(payload.get("patterns"), list) else []
        return (
            f"bundle schema={schema_version} "
            f"profile={profile.get('display_name') or 'unknown'} "
            f"patterns={len(patterns)}"
        )
    if schema_version == "ai_snake_prediction_profile.v1":
        return (
            f"profile id={payload.get('profile_id') or 'unknown'} "
            f"name={payload.get('display_name') or 'unknown'} "
            f"patterns={len(payload.get('pattern_refs') or [])}"
        )
    if schema_version in {"ai_snake_learned_pattern.v1", "ai_snake_learned_pattern.v1-list"}:
        count = 1 if schema_version == "ai_snake_learned_pattern.v1" else len(payload.get("patterns") or [])
        return f"patterns schema={schema_version} count={count}"
    return f"schema={schema_version}"


if __name__ == "__main__":
    raise SystemExit(main())
