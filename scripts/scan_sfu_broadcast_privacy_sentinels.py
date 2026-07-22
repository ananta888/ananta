#!/usr/bin/env python3
"""Scan explicitly supplied SFU broadcast run surfaces for injected sentinels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from agent.services.sfu_broadcast_privacy_sentinel import (
    SfuBroadcastPrivacyScanConfigurationError,
    configuration_failure_report,
    scan_sfu_broadcast_privacy_surfaces,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-privacy-scan.json"
INPUT_DOCUMENT_BYTES_MAX = 1024 * 1024


def _read_document(path: Path) -> Mapping[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > INPUT_DOCUMENT_BYTES_MAX:
            raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_input_document_unavailable")
        document = json.loads(path.read_text(encoding="utf-8"))
    except SfuBroadcastPrivacyScanConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_input_document_unavailable") from exc
    if not isinstance(document, Mapping):
        raise SfuBroadcastPrivacyScanConfigurationError("sfu_privacy_input_document_invalid")
    return document


def run(*, root: Path, manifest_path: Path, sentinel_path: Path) -> dict[str, Any]:
    try:
        return scan_sfu_broadcast_privacy_surfaces(
            root=root,
            manifest_document=_read_document(manifest_path),
            sentinel_document=_read_document(sentinel_path),
        )
    except SfuBroadcastPrivacyScanConfigurationError as exc:
        return configuration_failure_report(exc.reason_code)
    except Exception:
        return configuration_failure_report("sfu_privacy_scan_internal_error")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the bounded SFU broadcast privacy sentinel scan.")
    parser.add_argument("--root", type=Path, required=True, help="Root containing explicitly listed run sources.")
    parser.add_argument("--manifest", type=Path, required=True, help="Content-free source manifest.")
    parser.add_argument("--sentinels", type=Path, required=True, help="Per-run injected sentinel values.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run(root=args.root, manifest_path=args.manifest, sentinel_path=args.sentinels)
    _write_report(args.output, report)
    print(json.dumps({"decision": report["decision"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["decision"] == "allow" else 1


if __name__ == "__main__":
    raise SystemExit(main())
