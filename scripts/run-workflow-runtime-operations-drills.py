#!/usr/bin/env python3
"""Run deterministic workflow-runtime migration, rollout and recovery drills."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agent.services.workflow_runtime.operations_drills import (  # noqa: E402
    OPERATIONS_DRILL_REPORT_SCHEMA,
    WorkflowRuntimeOperationsDrillSuite,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise backup/restore, Alembic N-1, authorization rotation and "
            "incident containment plus gated rollout/rollback in an isolated "
            "workspace."
        )
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="New empty drill workspace. Omit to use and remove a temporary directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON evidence destination; stdout always receives the report.",
    )
    parser.add_argument(
        "--source-revision",
        help="Release source revision. Defaults to the checked-out Git revision.",
    )
    return parser.parse_args()


def _source_revision(explicit: str | None) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode or not completed.stdout.strip():
        raise RuntimeError("workflow_operations_source_revision_unavailable")
    checked_out = completed.stdout.strip()
    content_digest = _dirty_tree_digest()
    effective = (
        checked_out
        if content_digest is None
        else f"{checked_out}+dirty.sha256.{content_digest}"
    )
    if explicit and explicit.strip() and explicit.strip() != effective:
        raise RuntimeError("workflow_operations_source_revision_mismatch")
    return effective


def _dirty_tree_digest() -> str | None:
    """Bind drill evidence to dirty tracked and untracked repository bytes."""

    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    if diff.returncode or untracked.returncode:
        raise RuntimeError("workflow_operations_source_content_unavailable")
    paths = tuple(value for value in untracked.stdout.split(b"\0") if value)
    if not diff.stdout and not paths:
        return None
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(diff.stdout)
    for encoded_path in sorted(paths):
        try:
            relative = encoded_path.decode("utf-8")
        except UnicodeError as exc:
            raise RuntimeError("workflow_operations_source_content_unverified") from exc
        path = REPOSITORY_ROOT / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimeError("workflow_operations_source_content_unverified") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("workflow_operations_source_content_unverified")
        digest.update(b"\0untracked\0")
        digest.update(encoded_path)
        digest.update(b"\0")
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise RuntimeError("workflow_operations_source_content_unverified") from exc
    return digest.hexdigest()


def _write_output(path: Path, payload: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = _arguments()
    automatic_workspace = args.workspace is None
    temporary_root = Path(tempfile.mkdtemp(prefix="ananta-workflow-operations-drill-")) if automatic_workspace else None
    workspace = temporary_root / "workspace" if automatic_workspace else args.workspace.resolve()
    try:
        report = WorkflowRuntimeOperationsDrillSuite(repository_root=REPOSITORY_ROOT).run(
            workspace=workspace,
            source_revision=_source_revision(args.source_revision),
        )
        payload = report.to_dict()
        if args.output is not None:
            _write_output(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # fail closed at the command boundary
        reason = str(exc)
        if not reason.startswith("workflow_operations_") or any(character.isspace() for character in reason):
            reason = "workflow_operations_drill_failed"
        failure = {
            "schema": OPERATIONS_DRILL_REPORT_SCHEMA,
            "status": "failed",
            "reason_code": reason,
        }
        if args.output is not None:
            _write_output(args.output, failure)
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 1
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
