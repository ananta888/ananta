from __future__ import annotations

from typing import Any


_ALLOWED_TOOL_HINTS = {"pytest", "python", "npm", "node", "bash", "git", "docker", "poetry", "pip"}


def analyze_semantic_behavior(*, subtasks: list[dict[str, Any]], parallel_default: bool = False) -> list[str]:
    codes: list[str] = []
    if not subtasks:
        return ["architecture_only_no_execution"]

    coding_nodes = [s for s in subtasks if str(s.get("task_kind") or "").lower() in {"coding", "testing", "ops"}]
    if coding_nodes:
        missing_art = [s for s in coding_nodes if not list(s.get("expected_artifacts") or [])]
        if missing_art:
            codes.append("missing_artifacts_for_coding")
        missing_ver = [s for s in coding_nodes if not isinstance(s.get("verification_spec"), dict) or not s.get("verification_spec")]
        if missing_ver:
            codes.append("missing_verification_for_execution")

    total = len(subtasks)
    sequential_like = 0
    parallel_like = 0
    for i, s in enumerate(subtasks, start=1):
        deps = list(s.get("depends_on") or [])
        mode = str(s.get("dependency_mode") or "").lower()
        if mode == "parallel" or not deps:
            parallel_like += 1
        if deps and (str(i - 1) in [str(d) for d in deps] or any(str(d).endswith(f"node-{i-1}") for d in deps if isinstance(d, str))):
            sequential_like += 1
    if total >= 3 and sequential_like >= total - 1 and parallel_default:
        codes.append("sequentializes_everything")
    if total >= 3 and parallel_like == total and not parallel_default:
        codes.append("over_parallelizes_everything")

    for s in subtasks:
        txt = f"{s.get('title','')} {s.get('description','')}".lower()
        if "tool:" in txt or "use tool" in txt:
            if not any(tok in txt for tok in _ALLOWED_TOOL_HINTS):
                codes.append("hallucinated_tools")
                break

    if all(len(str(s.get("description") or "")) < 20 for s in subtasks):
        codes.append("too_abstract_tasks")

    dedup: list[str] = []
    for c in codes:
        if c not in dedup:
            dedup.append(c)
    return dedup
