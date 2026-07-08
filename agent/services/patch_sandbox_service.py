"""Apply candidate patches in an isolated temporary workspace."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class PatchSandboxService:
    def create_sandbox(
        self,
        *,
        workspace_dir: str | Path,
        patch_text: str,
        base_ref: str = "workspace",
    ) -> dict[str, Any]:
        root = Path(workspace_dir).resolve()
        patch = str(patch_text or "")
        if self._has_unsafe_patch_path(patch):
            return {
                "schema": "patch_sandbox.v1",
                "status": "failed",
                "reason_code": "unsafe_patch_path",
                "base_ref": base_ref,
            }
        sandbox = Path(tempfile.mkdtemp(prefix="ananta-perf-sandbox-"))
        ignore = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules")
        shutil.copytree(root, sandbox / "workspace", dirs_exist_ok=True, ignore=ignore)
        target = sandbox / "workspace"
        patch_file = sandbox / "candidate.patch"
        patch_file.write_text(patch, encoding="utf-8")
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_file)],
            cwd=target,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "schema": "patch_sandbox.v1",
                "status": "failed",
                "reason_code": "patch_apply_failed",
                "sandbox_dir": str(target),
                "base_ref": base_ref,
                "patch_hash": self._hash(patch),
                "stderr": completed.stderr,
            }
        changed = self._changed_files(target)
        return {
            "schema": "patch_sandbox.v1",
            "status": "completed",
            "reason_code": "success",
            "sandbox_dir": str(target),
            "base_ref": base_ref,
            "patch_hash": self._hash(patch),
            "changed_files": changed,
            "rollback_info": {"mode": "delete_sandbox", "path": str(sandbox)},
        }

    @staticmethod
    def _has_unsafe_patch_path(patch: str) -> bool:
        for line in patch.splitlines():
            if line.startswith(("--- ", "+++ ", "diff --git ")):
                if "/.." in line or "../" in line:
                    return True
                parts = line.split()
                for part in parts[1:]:
                    cleaned = part.removeprefix("a/").removeprefix("b/")
                    if Path(cleaned).is_absolute():
                        return True
        return False

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _changed_files(path: Path) -> list[str]:
        completed = subprocess.run(
            ["git", "diff", "--name-only", "--no-index", "/dev/null", "."],
            cwd=path,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        names = []
        for line in completed.stdout.splitlines():
            if line and not line.startswith("/dev/null"):
                names.append(line.removeprefix("./"))
        return sorted(set(names))


def get_patch_sandbox_service() -> PatchSandboxService:
    return PatchSandboxService()
