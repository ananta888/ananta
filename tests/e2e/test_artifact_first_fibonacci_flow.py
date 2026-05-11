"""AFH-T023: E2E regression — Fibonacci Flask files complete despite malformed final JSON.

The exact observed failure mode: worker produces valid files but returns Markdown/chat
as final response. Hub must complete or needs_review without infinite retry.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from agent.services.artifact_manifest_service import get_artifact_manifest_service
from agent.services.planning_utils import parse_followup_analysis
from agent.services.task_completion_policy_service import get_task_completion_policy_service
from agent.services.task_retry_policy_service import (
    get_task_retry_policy_service,
    REASON_ADVISORY_JSON_PARSE_FAILED,
)
from agent.services.worker_output_collector_service import get_worker_output_collector_service
from worker.core.artifact_manifest import build_artifact_manifest, write_manifest


FIBONACCI_FILES = {
    "app.py": (
        "from flask import Flask\nimport math\napp = Flask(__name__)\n"
        "@app.route('/fib/<int:n>')\ndef fib(n): return str(sum(...))\n"
    ),
    "requirements.txt": "flask>=2.0\n",
    "README.md": "# Fibonacci Flask\nA simple Fibonacci REST API.\n",
}


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# Malformed JSON final responses that should NOT cause retry loops
MALFORMED_FINAL_RESPONSES = [
    "Great news! I have successfully created the Fibonacci Flask application with all required files.",
    "```python\n# app.py created\n```\nAll done!",
    "The task is complete. Files generated:\n- app.py\n- requirements.txt\n- README.md",
    '{"incomplete": true',
    "task_complete",
    "",
]


@pytest.fixture
def fibonacci_workspace(tmp_path: Path) -> Path:
    """Create a fibonacci_project workspace with the expected files."""
    ws = tmp_path / "fibonacci_project"
    ws.mkdir()
    for filename, content in FIBONACCI_FILES.items():
        (ws / filename).write_text(content, encoding="utf-8")
    return ws


@pytest.fixture
def fibonacci_manifest(fibonacci_workspace: Path) -> dict:
    """Build a valid artifact manifest for the Fibonacci project."""
    artifacts = []
    for filename, content in FIBONACCI_FILES.items():
        artifacts.append({
            "artifact_id": f"art-{uuid.uuid4().hex[:12]}",
            "kind": "generated_file",
            "relative_path": filename,
            "content_hash": _sha256(content),
            "size_bytes": len(content.encode("utf-8")),
            "classification": "internal",
            "operation": "created",
            "required": True,
            "verification_status": "pending",
            "metadata": {},
        })
    return build_artifact_manifest(
        goal_id="goal-fibonacci",
        task_id="task-fibonacci",
        execution_id="exec-fibonacci",
        trace_id="tr-fibonacci",
        workspace_root=fibonacci_workspace,
        worker_id="test-worker",
        artifacts=artifacts,
        summary="Fibonacci Flask project files",
    )


@pytest.mark.parametrize("malformed_response", MALFORMED_FINAL_RESPONSES)
def test_fibonacci_completes_despite_malformed_final_json(
    fibonacci_workspace: Path,
    fibonacci_manifest: dict,
    malformed_response: str,
    tmp_path: Path,
) -> None:
    """Task must complete (or needs_review) even when final model response is not valid JSON."""
    # Write manifest to workspace
    manifest_dir = fibonacci_workspace / ".ananta" / "handoff" / "exec-fibonacci"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "artifact_manifest.v1.json"
    write_manifest(fibonacci_manifest, manifest_path)

    # Parse the malformed final response as advisory
    advisory = parse_followup_analysis(malformed_response)
    assert advisory["advisory"] is True, "parse_followup_analysis must always be advisory"

    # If response is non-JSON, task_complete must be None (not True/False)
    if malformed_response.strip() and not malformed_response.strip().startswith("{"):
        # Only check for genuinely non-JSON responses
        try:
            json.loads(malformed_response)
            is_json = True
        except (json.JSONDecodeError, ValueError):
            is_json = False
        if not is_json:
            assert advisory["task_complete"] is None, (
                f"Malformed response {malformed_response!r}: task_complete must be None, "
                "not True/False. Malformed JSON must not drive retry loops."
            )

    # Collect artifacts (manifest exists)
    collector = get_worker_output_collector_service()
    collection = collector.collect(
        task_id="task-fibonacci",
        goal_id="goal-fibonacci",
        execution_id="exec-fibonacci",
        trace_id="tr-fibonacci",
        workspace_root=fibonacci_workspace,
        manifest_relative_path=".ananta/handoff/exec-fibonacci/artifact_manifest.v1.json",
        allow_synthesized_fallback=False,
    )
    assert collection["manifest_valid"], f"Manifest must be valid. errors={collection.get('errors')}"
    assert len(collection["artifacts"]) == 3

    # Make completion decision — advisory parse failure must NOT cause retry
    completion_svc = get_task_completion_policy_service()
    decision = completion_svc.evaluate(
        task_id="task-fibonacci",
        collection_result=collection,
        advisory_parse_result=advisory,
        exit_code=0,
        retry_count=0,
        expected_paths=["app.py", "requirements.txt", "README.md"],
    )

    assert decision.decision in ("completed", "needs_review"), (
        f"Task must be completed or needs_review, not {decision.decision!r}. "
        f"Files exist and are valid; malformed final JSON must not cause failure or retry."
    )

    # Verify retry policy never requeues for advisory parse failure with valid artifacts
    retry_svc = get_task_retry_policy_service()
    retry_cls = retry_svc.classify(
        reason=REASON_ADVISORY_JSON_PARSE_FAILED,
        retry_count=0,
        has_valid_artifacts=True,
    )
    assert not retry_cls.should_retry, (
        "Advisory parse failure with valid artifacts must NEVER requeue the task."
    )

    # Advisory parse status must be recorded separately from completion decision
    if advisory.get("parse_error"):
        assert "advisory_parse_failed_ignored" in decision.reason_codes, (
            "reason_codes must include advisory_parse_failed_ignored when advisory parse failed "
            "but artifact completion succeeded."
        )


def test_fibonacci_without_manifest_uses_needs_review_not_retry(
    fibonacci_workspace: Path,
) -> None:
    """Without manifest, task must reach needs_review/failed, not infinite retry."""
    # No manifest written
    advisory = parse_followup_analysis("Here are the files I created: app.py, requirements.txt")
    assert advisory["parse_error"] is True

    collector = get_worker_output_collector_service()
    collection = collector.collect(
        task_id="task-fibonacci-no-manifest",
        goal_id="goal-fibonacci",
        execution_id="exec-fibonacci-2",
        trace_id="tr-fibonacci-2",
        workspace_root=fibonacci_workspace,
        manifest_relative_path=".ananta/handoff/exec-fibonacci-2/artifact_manifest.v1.json",
        allow_synthesized_fallback=False,
    )
    assert not collection["manifest_valid"]

    completion_svc = get_task_completion_policy_service()
    decision = completion_svc.evaluate(
        task_id="task-fibonacci-no-manifest",
        collection_result=collection,
        advisory_parse_result=advisory,
        exit_code=0,
        retry_count=3,  # At max retries
        expected_paths=["app.py", "requirements.txt", "README.md"],
    )
    # At max retries, must be failed or needs_review — not retryable
    assert decision.decision in ("failed", "needs_review"), (
        f"At max retries, decision must be failed or needs_review, got {decision.decision!r}"
    )


def test_fibonacci_with_synthesized_manifest_when_allowed(
    fibonacci_workspace: Path,
) -> None:
    """Synthesized manifest from workspace diff can complete task when policy allows."""
    from agent.services.workspace_diff_service import get_workspace_diff_service

    diff_svc = get_workspace_diff_service()
    before_id, before_snap = diff_svc.take_before_snapshot(fibonacci_workspace)
    after_id, after_snap = diff_svc.take_after_snapshot(fibonacci_workspace)

    fcs = diff_svc.compute_diff(
        task_id="task-fibonacci-synth",
        execution_id="exec-fibonacci-synth",
        workspace_root=fibonacci_workspace,
        before_snapshot_id=before_id,
        before_snapshot={},
        after_snapshot_id=after_id,
        after_snapshot=after_snap,
    )
    assert len(fcs.created_files) == len(FIBONACCI_FILES)

    synth = diff_svc.synthesize_manifest(
        file_change_set=fcs,
        workspace_root=fibonacci_workspace,
        task_id="task-fibonacci-synth",
        goal_id="goal-fibonacci",
        execution_id="exec-fibonacci-synth",
        trace_id="tr-fibonacci-synth",
    )
    assert synth["synthesized"] is True
    assert len(synth["artifacts"]) == len(FIBONACCI_FILES)

    manifest_svc = get_artifact_manifest_service()
    validation = manifest_svc.validate_manifest(synth, workspace_root=fibonacci_workspace)
    assert validation["valid"], f"Synthesized manifest must be valid: {validation['errors']}"

    completion_svc = get_task_completion_policy_service()
    decision = completion_svc.evaluate(
        task_id="task-fibonacci-synth",
        collection_result={**validation, "synthesized": True},
        allow_synthesized_manifest=True,
    )
    assert decision.decision in ("completed", "needs_review")
