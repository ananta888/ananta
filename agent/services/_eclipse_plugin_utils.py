from typing import Any


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(profile.get("id") or "default", max_chars=80),
        "base_url": _clean_text(profile.get("base_url") or "http://localhost:8080", max_chars=240),
        "auth_method": _clean_text(profile.get("auth_method") or "session_token", max_chars=40).lower(),
        "environment": _clean_text(profile.get("environment") or "local", max_chars=40).lower(),
    }


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(task.get("id"), max_chars=100),
        "title": _clean_text(task.get("title"), max_chars=200),
        "status": _clean_text(task.get("status") or "todo", max_chars=30),
        "goal_id": _clean_text(task.get("goal_id"), max_chars=100) or None,
        "review_required": bool(task.get("review_required", False)),
        "next_step": _clean_text(task.get("next_step"), max_chars=180) or None,
    }


def _compact_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(artifact.get("id"), max_chars=100),
        "title": _clean_text(artifact.get("title"), max_chars=200),
        "type": _clean_text(artifact.get("type"), max_chars=60),
        "task_id": _clean_text(artifact.get("task_id"), max_chars=100) or None,
    }


def build_eclipse_diff_review_render(
    proposals: list[dict[str, Any]],
    *,
    max_hunks: int = 12,
) -> dict[str, Any]:
    rendered = []
    for proposal in proposals or []:
        hunks = list(proposal.get("hunks") or [])[: max(1, int(max_hunks))]
        file_references = []
        for hunk in hunks:
            if not isinstance(hunk, dict):
                continue
            path = _clean_text(hunk.get("path"), max_chars=400)
            if not path:
                continue
            file_references.append(
                {
                    "path": path,
                    "line": int(hunk.get("line")) if str(hunk.get("line") or "").isdigit() else None,
                }
            )
        rendered.append(
            {
                "proposal_id": _clean_text(proposal.get("id"), max_chars=100),
                "title": _clean_text(proposal.get("title"), max_chars=200),
                "hunks": hunks,
                "file_references": file_references,
                "auto_apply": False,
            }
        )
    return {
        "schema": "eclipse_diff_review_render_v1",
        "proposals": rendered,
        "readable_in_ide": True,
        "clickable_file_references_where_possible": True,
        "never_auto_apply_visible_changes": True,
    }
