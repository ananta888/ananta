"""Validate the Ananta pattern catalog (PAT-018).

Checks:
- Catalog JSON loads and is a list of pattern dicts
- Each pattern has required fields (pattern_id, version, language, title, parameters, templates)
- All pattern_ids are unique and match the allowed id pattern
- All referenced template files exist on disk
- Optional: --render-examples dry-runs one Strategy plan per language

Exit codes:
  0  all checks passed
  1  one or more checks failed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "schemas" / "patterns" / "pattern_catalog.v1.json"
VALID_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate Ananta pattern catalog.")
    p.add_argument(
        "--catalog",
        default=str(DEFAULT_CATALOG),
        help="Path to pattern catalog JSON (default: schemas/patterns/pattern_catalog.v1.json).",
    )
    p.add_argument(
        "--render-examples",
        action="store_true",
        help="Dry-run render one example plan per java.strategy / python.strategy / ts.strategy.",
    )
    return p.parse_args()


def _error(errors: list[str], msg: str) -> None:
    errors.append(msg)
    print(f"  ERROR: {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"  WARN:  {msg}", file=sys.stderr)


def validate_catalog(catalog_path: Path) -> list[str]:
    errors: list[str] = []
    print(f"Validating catalog: {catalog_path}")

    if not catalog_path.exists():
        return [f"catalog file not found: {catalog_path}"]

    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"catalog JSON parse error: {exc}"]

    if not isinstance(raw, list):
        return [f"catalog must be a JSON array, got {type(raw).__name__}"]

    print(f"  {len(raw)} pattern(s) found.")
    seen_ids: set[str] = set()

    for idx, entry in enumerate(raw):
        prefix = f"patterns[{idx}]"
        if not isinstance(entry, dict):
            _error(errors, f"{prefix}: entry must be an object, got {type(entry).__name__}")
            continue

        pid = str(entry.get("pattern_id") or "").strip()
        if not pid:
            _error(errors, f"{prefix}: missing pattern_id")
        elif not VALID_ID.match(pid):
            _error(errors, f"{prefix}: pattern_id {pid!r} must match ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$")
        elif pid in seen_ids:
            _error(errors, f"{prefix}: duplicate pattern_id {pid!r}")
        else:
            seen_ids.add(pid)

        for required in ("version", "language", "title"):
            if not str(entry.get(required) or "").strip():
                _error(errors, f"{prefix} ({pid}): missing required field {required!r}")

        params = entry.get("parameters")
        if params is not None and not isinstance(params, list):
            _error(errors, f"{prefix} ({pid}): parameters must be a list")

        templates = entry.get("templates")
        if templates is not None:
            if not isinstance(templates, list):
                _error(errors, f"{prefix} ({pid}): templates must be a list")
            else:
                for tidx, tmpl in enumerate(templates):
                    if not isinstance(tmpl, dict):
                        _error(errors, f"{prefix} ({pid}): templates[{tidx}] must be an object")
                        continue
                    tmpl_path = str(tmpl.get("path") or "").strip()
                    if not tmpl_path:
                        _error(errors, f"{prefix} ({pid}): templates[{tidx}] missing path")
                    elif ".." in tmpl_path or tmpl_path.startswith("/"):
                        _error(errors, f"{prefix} ({pid}): templates[{tidx}] path traversal not allowed: {tmpl_path!r}")
                    else:
                        full_path = ROOT / tmpl_path
                        if not full_path.exists():
                            _error(errors, f"{prefix} ({pid}): template file not found: {tmpl_path}")
                        else:
                            print(f"    OK template: {tmpl_path}")

    return errors


def dry_run_examples(catalog_path: Path) -> list[str]:
    """Dry-run render for java.strategy, python.strategy, ts.strategy."""
    errors: list[str] = []
    print("\nDry-run rendering examples:")
    try:
        sys.path.insert(0, str(ROOT))
        from agent.services.pattern_template_renderer import PatternTemplateRenderer, TemplateFile
        from agent.services.pattern_registry import get_registry
    except ImportError as exc:
        return [f"import failed (run inside the venv): {exc}"]

    registry = get_registry()
    renderer = PatternTemplateRenderer()

    example_plans = [
        {
            "pattern_id": "java.strategy",
            "language": "java",
            "parameters": {"context_class": "Order", "package_name": "com.example.demo"},
        },
        {
            "pattern_id": "python.strategy",
            "language": "python",
            "parameters": {"context_class": "Order"},
        },
        {
            "pattern_id": "ts.strategy",
            "language": "typescript",
            "parameters": {"context_class": "Order"},
        },
    ]

    for plan in example_plans:
        pid = plan["pattern_id"]
        entry = registry.get(pid)
        if entry is None:
            _warn(f"{pid}: not found in registry, skipping")
            continue
        templates_cfg = entry.get("templates") or []
        tmpl_files = []
        ok = True
        for t in templates_cfg:
            path = ROOT / str(t.get("path") or "")
            if not path.exists():
                _error(errors, f"{pid}: template file missing: {t.get('path')}")
                ok = False
                continue
            tmpl_files.append(TemplateFile(
                name=str(t.get("name") or ""),
                output_filename=path.name.replace(".tmpl", ""),
                content=path.read_text(encoding="utf-8"),
            ))
        if not ok:
            continue
        try:
            manifest = renderer.render(
                pattern_plan=plan,
                templates=tmpl_files,
                target_root=None,
            )
            print(f"  OK dry-run: {pid} → {len(manifest.files)} file(s), hash={manifest.manifest_sha256[:12]}")
        except Exception as exc:
            _error(errors, f"{pid}: render failed: {exc}")

    return errors


def main() -> None:
    args = _parse_args()
    catalog_path = Path(args.catalog).resolve()
    errors = validate_catalog(catalog_path)

    if args.render_examples:
        errors += dry_run_examples(catalog_path)

    if errors:
        print(f"\nFAILED: {len(errors)} error(s).", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nAll checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
