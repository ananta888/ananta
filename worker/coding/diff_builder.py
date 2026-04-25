from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _run_git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout or completed.stderr or "").strip() or f"git {' '.join(args)} failed")
    return completed.stdout or ""


@dataclass(frozen=True)
class BuiltDiff:
    diff: str
    patch_hash: str
    changed_files: list[str]
    base_ref: str

    def as_artifact(self, *, task_id: str, capability_id: str, risk_classification: str = "high") -> dict:
        return {
            "schema": "patch_artifact.v1",
            "task_id": str(task_id).strip(),
            "capability_id": str(capability_id).strip(),
            "base_ref": self.base_ref,
            "patch": self.diff,
            "patch_hash": self.patch_hash,
            "changed_files": list(self.changed_files),
            "risk_classification": str(risk_classification).strip() or "high",
        }


def build_unified_diff(*, repository_root: Path, base_ref: str = "HEAD") -> BuiltDiff:
    repo = repository_root.resolve()
    diff_parts: list[str] = []
    staged_or_tracked = _run_git(["diff", "--no-color", "--relative", str(base_ref)], cwd=repo)
    if staged_or_tracked.strip():
        diff_parts.append(staged_or_tracked)

    untracked = _run_git(["ls-files", "--others", "--exclude-standard"], cwd=repo)
    untracked_files = [line.strip() for line in untracked.splitlines() if line.strip()]
    if untracked_files:
        # include untracked files in unified diff form by comparing against /dev/null
        for rel_path in untracked_files:
            file_path = repo / rel_path
            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            escaped = rel_path.replace("\\", "/")
            diff_parts.append(
                "\n".join(
                    [
                        f"diff --git a/{escaped} b/{escaped}",
                        "new file mode 100644",
                        "index 0000000..0000000",
                        f"--- /dev/null",
                        f"+++ b/{escaped}",
                        f"@@ -0,0 +1,{max(1, len(lines))} @@",
                        *([f"+{line}" for line in lines] if lines else ["+"]),
                        "",
                    ]
                )
            )

    status_lines = _run_git(["status", "--porcelain"], cwd=repo)
    changed_files: list[str] = []
    for line in status_lines.splitlines():
        if not line.strip():
            continue
        rel_path = _status_path(line)
        if rel_path and rel_path not in changed_files:
            changed_files.append(rel_path)

    joined_diff = "".join(diff_parts)
    patch_hash = hashlib.sha256(joined_diff.encode("utf-8")).hexdigest()
    return BuiltDiff(diff=joined_diff, patch_hash=patch_hash, changed_files=changed_files, base_ref=str(base_ref))


def _status_path(line: str) -> str:
    if len(line) >= 4 and line[2] == " ":
        return line[3:].strip()
    if len(line) >= 3:
        return line[2:].strip()
    return line.strip()
