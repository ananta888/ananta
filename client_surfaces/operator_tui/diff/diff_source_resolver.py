from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent.artifacts.goal_artifact_service import GoalArtifactService


def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _read_file(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("binary_content")
    return raw.decode("utf-8")


class DiffSourceResolver:
    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        goal_artifact_service: GoalArtifactService | None = None,
    ) -> None:
        self._repo_root = Path(repo_root or Path.cwd()).resolve()
        self._goal_artifact_service = goal_artifact_service or GoalArtifactService()

    def resolve(self, source_ref: dict[str, Any], *, goal_id: str | None = None) -> dict[str, Any]:
        source_kind = str(source_ref.get("source_kind") or "").strip()
        locator = dict(source_ref.get("locator") or {})
        base_result = {
            "source_ref_id": str(source_ref.get("source_ref_id") or ""),
            "source_kind": source_kind,
            "display_name": str(source_ref.get("display_name") or source_kind),
        }
        try:
            if source_kind in {"git_diff", "working_tree"}:
                return {**base_result, **self._resolve_current_diff(locator)}
            if source_kind == "file_path":
                return {**base_result, **self._resolve_file_vs_head(locator)}
            if source_kind == "git_ref":
                return {**base_result, **self._resolve_git_ref_pair(locator)}
            if source_kind in {"artifact_ref", "goal_output_artifact", "task_output", "snapshot", "inline_text"}:
                return {**base_result, **self._resolve_artifact_text(source_kind, locator, goal_id=goal_id)}
            return {**base_result, "ok": False, "reason_code": "unsupported_source_kind"}
        except FileNotFoundError:
            return {**base_result, "ok": False, "reason_code": "source_not_found"}
        except ValueError as exc:
            if str(exc) == "binary_content":
                return {**base_result, "ok": False, "reason_code": "binary_unsupported"}
            return {**base_result, "ok": False, "reason_code": "invalid_source"}
        except Exception:
            return {**base_result, "ok": False, "reason_code": "resolver_failed"}

    def _resolve_current_diff(self, locator: dict[str, Any]) -> dict[str, Any]:
        base_ref = str(locator.get("base_ref") or "HEAD")
        path_filter = str(locator.get("path_filter") or "").strip()
        args = ["--no-pager", "diff", "--no-ext-diff", base_ref]
        if path_filter:
            args.extend(["--", path_filter])
        code, stdout, _ = _run_git(args, cwd=self._repo_root)
        if code != 0:
            return {"ok": False, "reason_code": "git_diff_failed"}
        return {"ok": True, "content_type": "patch", "patch": stdout}

    def _resolve_file_vs_head(self, locator: dict[str, Any]) -> dict[str, Any]:
        rel_path = str(locator.get("path") or "").strip()
        if not rel_path:
            return {"ok": False, "reason_code": "path_required"}
        against = str(locator.get("against") or "HEAD")
        worktree_file = self._repo_root / rel_path
        code, old_text, _ = _run_git(["show", f"{against}:{rel_path}"], cwd=self._repo_root)
        if code != 0:
            return {"ok": False, "reason_code": "git_show_failed"}
        if not worktree_file.exists():
            return {"ok": False, "reason_code": "source_not_found"}
        return {
            "ok": True,
            "content_type": "pair",
            "path": rel_path,
            "left_ref": against,
            "right_ref": "working_tree",
            "left_text": old_text,
            "right_text": _read_file(worktree_file),
        }

    def _resolve_git_ref_pair(self, locator: dict[str, Any]) -> dict[str, Any]:
        left_ref = str(locator.get("left_ref") or "").strip()
        right_ref = str(locator.get("right_ref") or "").strip()
        rel_path = str(locator.get("path") or "").strip()
        if not left_ref or not right_ref or not rel_path:
            return {"ok": False, "reason_code": "left_right_path_required"}
        code_l, left_text, _ = _run_git(["show", f"{left_ref}:{rel_path}"], cwd=self._repo_root)
        code_r, right_text, _ = _run_git(["show", f"{right_ref}:{rel_path}"], cwd=self._repo_root)
        if code_l != 0 or code_r != 0:
            return {"ok": False, "reason_code": "git_show_failed"}
        return {
            "ok": True,
            "content_type": "pair",
            "path": rel_path,
            "left_ref": left_ref,
            "right_ref": right_ref,
            "left_text": left_text,
            "right_text": right_text,
        }

    def _resolve_artifact_text(self, source_kind: str, locator: dict[str, Any], *, goal_id: str | None) -> dict[str, Any]:
        if source_kind == "inline_text":
            return {"ok": True, "content_type": "text", "text": str(locator.get("text") or "")}
        if source_kind == "goal_output_artifact":
            goal = str(goal_id or locator.get("goal_id") or "").strip()
            output_artifact_id = str(locator.get("output_artifact_id") or "").strip()
            if not goal or not output_artifact_id:
                return {"ok": False, "reason_code": "goal_or_output_required"}
            graph = self._goal_artifact_service.get_goal_graph(goal)
            output = next(
                (
                    item
                    for item in list(graph.get("output_artifacts") or [])
                    if str(item.get("output_artifact_id") or "") == output_artifact_id
                ),
                None,
            )
            if output is None:
                return {"ok": False, "reason_code": "output_not_found"}
            artifact_ref = str(output.get("artifact_ref") or "").strip()
            text = self._resolve_artifact_ref_to_text(artifact_ref)
            if text is None:
                return {"ok": False, "reason_code": "artifact_content_not_found"}
            return {
                "ok": True,
                "content_type": "text",
                "text": text,
                "artifact_ref": artifact_ref,
                "output_artifact_id": output_artifact_id,
                "provenance_id": str(output.get("provenance_id") or ""),
                "task_id": str(output.get("task_id") or ""),
                "worker_id": str(output.get("worker_id") or ""),
            }
        artifact_ref = str(locator.get("artifact_ref") or "").strip()
        text = self._resolve_artifact_ref_to_text(artifact_ref)
        if text is None:
            return {"ok": False, "reason_code": "artifact_content_not_found"}
        return {"ok": True, "content_type": "text", "text": text, "artifact_ref": artifact_ref}

    def _resolve_artifact_ref_to_text(self, artifact_ref: str) -> str | None:
        if not artifact_ref:
            return None
        if artifact_ref.startswith("file:"):
            path = Path(artifact_ref.removeprefix("file:"))
            if not path.is_absolute():
                path = (self._repo_root / path).resolve()
            if not path.exists():
                return None
            return _read_file(path)
        return None
