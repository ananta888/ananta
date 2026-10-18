"""Queue-delegated independent acceptance review as a separate Hub-controlled task.

The Hub owns reviewer resolution, Registry run reservation, snapshot export and
verdict verification. Workers never select themselves and never see the private
control directory. The review Task flows through the ordinary task queue, so the
development dispatch is released while the review runs.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict
from typing import Any, Callable

from agent.services.hub_evidence_gate_service import canonical_evidence_digest
from agent.services.self_development.acceptance_review import (
    AcceptanceReviewError,
    AcceptanceReviewReceipt,
    AcceptanceReviewRequest,
    AcceptanceReviewResult,
)
from agent.services.self_development.workspace import export_revision, read_source

PROJECT_ID = "ananta-self-development"
VERDICT_CONTRACT = '{"approved": <true|false>, "criteria_passed": [<true|false>, ...], "findings": ["...", ...]}'
_MAX_ANSWER_CHARS = 16000


class ReviewWorkflowError(ValueError):
    """Bounded, machine-readable review workflow failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def select_reviewer_worker(author_worker_url: str, *, workers: list | None = None) -> str:
    """Resolve an eligible, online reviewer Worker distinct from the author.

    Review-capable Workers win over generalists; ties break by URL so the choice
    never depends on model text or iteration order.
    """
    if workers is None:
        from agent.services.autopilot_support_service import available_workers

        workers = available_workers()
    author = str(author_worker_url or "").rstrip("/")
    review_roles = {"reviewer"}
    review_capabilities = {"review", "analysis"}
    eligible = []
    for worker in workers or []:
        url = str(getattr(worker, "url", "") or "").rstrip("/")
        if not url or url == author:
            continue
        roles = set(getattr(worker, "worker_roles", None) or [])
        capabilities = set(getattr(worker, "capabilities", None) or [])
        capable = bool(roles & review_roles or capabilities & review_capabilities)
        eligible.append((not capable, url, worker))
    if not eligible:
        raise AcceptanceReviewError("independent_acceptance_reviewer_unavailable")
    return min(eligible, key=lambda row: (row[0], row[1]))[1]


def build_review_brief(*, todo_id: str, requirements: dict, changed_files: dict, verification_command: str) -> str:
    """Bounded reviewer assignment; JSON data only, never execution authority."""
    criteria = requirements.get("acceptance_criteria")
    criteria = criteria if isinstance(criteria, list) else []
    lines = [
        "Independent acceptance review. You are a separate reviewer, not the author.",
        "Judge ONLY whether the changed files satisfy the acceptance criteria.",
        "",
        f"TODO: {todo_id}",
        "Task requirements (JSON data):",
        json.dumps(requirements, ensure_ascii=False, sort_keys=True),
        "",
        "Changed files under review (path: sha256):",
        *(f"- {path}: {digest}" for path, digest in sorted(changed_files.items())),
        "",
        "Read the changed files in this workspace before judging. Do not modify any file.",
    ]
    if criteria:
        lines.append("")
        lines.append(
            "Judge every criterion in order; criteria_passed must have exactly "
            f"{len(criteria)} entries in the same order."
        )
    lines += [
        "",
        "Then run exactly one verification via test.run: " + verification_command,
        'Finish with kind "final_answer" and an "answer" containing ONLY this JSON object: ' + VERDICT_CONTRACT,
        "approved is true only when every criterion passes and findings is empty.",
    ]
    brief = "\n".join(lines)
    if len(brief) > _MAX_ANSWER_CHARS:
        raise ReviewWorkflowError("review_brief_too_large")
    return brief


def parse_review_verdict(answer: str, *, criteria_count: int) -> AcceptanceReviewResult:
    """Strict bounded parse of the reviewer verdict; anything else fails closed."""
    text = str(answer or "")[:_MAX_ANSWER_CHARS]
    payload = None
    candidates = [text.strip()]
    # Prose-wrapped answers: try the last brace positions first, newest complete
    # object wins, bounded work.
    positions = [match.start() for match in re.finditer(r"\{", text)][-16:]
    candidates.extend(text[start:] for start in reversed(positions))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    if payload is None:
        raise ReviewWorkflowError("worker_review_verdict_unparseable")
    approved = payload.get("approved")
    passed = payload.get("criteria_passed")
    findings = payload.get("findings")
    if (
        not isinstance(approved, bool)
        or not isinstance(passed, list)
        or not all(isinstance(value, bool) for value in passed)
        or not isinstance(findings, list)
        or not all(isinstance(value, str) for value in findings)
    ):
        raise ReviewWorkflowError("worker_review_verdict_unparseable")
    if criteria_count < 1 or len(passed) != criteria_count:
        raise ReviewWorkflowError("worker_review_verdict_criteria_mismatch")
    return AcceptanceReviewResult(approved=approved, criteria_passed=tuple(passed), findings=tuple(findings))


class QueueAcceptanceReviewer:
    """Phased production reviewer: enqueue a queue Task, reserve its run, collect the receipt.

    ``enqueue_review`` runs inside the development dispatch and only admits the review
    (queue Task + reserved Registry run). ``begin_review``/``collect_review`` run inside
    the review Task's own dispatch, so the development dispatch is never blocked by the
    queue behind it.
    """

    def __init__(
        self,
        *,
        queue,
        registry,
        checkout,
        sealed_root,
        workspaces,
        tenant_id: str,
        team_id: str,
        policy_digest: str,
        choose_worker: Callable[[str], str] | None = None,
        task_lookup: Callable[[str], Any] | None = None,
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.checkout = checkout
        self.sealed_root = sealed_root
        self.workspaces = workspaces
        self.tenant_id = tenant_id
        self.team_id = team_id
        self.policy_digest = policy_digest
        self.choose_worker = choose_worker or select_reviewer_worker
        self.task_lookup = task_lookup or self._default_task_lookup

    @staticmethod
    def _default_task_lookup(task_id: str):
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_by_id(task_id)

    def review(self, request: AcceptanceReviewRequest) -> AcceptanceReviewReceipt:
        raise ReviewWorkflowError("phased_review_api_required")

    def enqueue_review(self, request: AcceptanceReviewRequest, *, todo_id: str) -> dict:
        """Admit a separate review Task with its Registry run reserved before execution."""
        reviewer = self.choose_worker(request.author_worker_url)
        review_task_id = str(uuid.uuid4())
        source = self.registry.register_source(
            tenant_id=self.tenant_id,
            project_id=PROJECT_ID,
            origin_type="acceptance-review",
            origin_digest=request.digest,
            content_digest=request.digest,
            policy_digest=self.policy_digest,
            evidence_scope="local",
            synthetic=False,
        )
        reserved = self.registry.reserve_run(
            tenant_id=self.tenant_id,
            project_id=PROJECT_ID,
            task_id=review_task_id,
            assignment_id=str(uuid.uuid4()),
            dispatch_lease_id=str(uuid.uuid4()),
            repository_revision=request.repository_revision,
            input_digest=request.digest,
            execution_profile_digest=canonical_evidence_digest({"phase": "acceptance-review"}),
            environment_digest=canonical_evidence_digest({"worker": reviewer, "phase": "acceptance-review"}),
            source_ids=[source.source_id],
            evidence_scope="local",
            synthetic=False,
            idempotency_key="acceptance-review:" + request.digest,
        )
        self.queue.ingest_task(
            task_id=review_task_id,
            status="todo",
            title="Independent acceptance review: " + (todo_id or request.task_id),
            description="Independent acceptance review; the Hub assignment binds the snapshot.",
            created_by="self-development-hub",
            source="system",
            team_id=self.team_id,
            tags=["self-development", "acceptance-review", todo_id],
            event_details={"review_of_task": request.task_id, "review_run_id": reserved.run_id},
            extra_fields={
                "task_kind": "gate_review",
                "required_capabilities": ["review", "analysis"],
                "assigned_agent_url": reviewer,
            },
        )
        return {
            "review_task_id": review_task_id,
            "reviewer_worker_url": reviewer,
            "run_id": reserved.run_id,
            "assignment_id": reserved.assignment_id,
            "dispatch_lease_id": reserved.dispatch_lease_id,
            "source_ids": list(reserved.source_ids),
        }

    def begin_review(self, request: AcceptanceReviewRequest, reservation: dict, *, verification_command: str) -> dict:
        """Export the immutable snapshot through the ordinary Worker workspace boundary."""
        reviewer = str(reservation.get("reviewer_worker_url") or "").rstrip("/")
        if not reviewer:
            raise ReviewWorkflowError("review_reservation_invalid")
        review_task_id = str(reservation.get("review_task_id") or "")
        workspace = self.workspaces / ("acceptance-review-" + review_task_id)
        export_revision(self.checkout, workspace, request.repository_revision)
        sealed_dir = self.sealed_root / request.task_id
        for relative, digest in request.changed_files:
            try:
                content = read_source(sealed_dir, relative)
            except ValueError as exc:
                raise ReviewWorkflowError("review_snapshot_unavailable:" + relative) from exc
            if hashlib.sha256(content).hexdigest() != digest:
                raise ReviewWorkflowError("review_snapshot_hash_mismatch:" + relative)
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o644)
        requirements = json.loads(request.requirements_json)
        changed_files = {path: digest for path, digest in request.changed_files}
        brief = build_review_brief(
            todo_id=str(requirements.get("todo_id") or ""),
            requirements=requirements,
            changed_files=changed_files,
            verification_command=verification_command,
        )
        manifest = {
            "schema": "self_development_review_brief.v1",
            "review_task_id": review_task_id,
            "review_run_id": reservation.get("run_id"),
            "author_task_id": request.task_id,
            "repository_revision": request.repository_revision,
            "requirements": requirements,
            "changed_files": changed_files,
            "verification_command": verification_command,
            "output_contract": VERDICT_CONTRACT,
            "final_answer_file": ".ananta/last-final-answer.json",
        }
        (workspace / ".ananta").mkdir(parents=True, exist_ok=True)
        (workspace / ".ananta" / "review.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        # The reviewer must observe and analyse, never write: the explicit research
        # context activates the mutation loop (required analysis gate) while the
        # absent materialization manifest keeps every write blocked.
        research = workspace / "rag_helper" / "research-context.json"
        research.parent.mkdir(parents=True, exist_ok=True)
        research.write_text(
            json.dumps(
                {"task_kind": "gate_review", "mutation_mode": "controlled_workspace"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        projection = self.registry.assignment_projection(
            tenant_id=self.tenant_id,
            project_id=PROJECT_ID,
            run_id=reservation["run_id"],
            task_id=review_task_id,
            assignment_id=reservation["assignment_id"],
            dispatch_lease_id=reservation["dispatch_lease_id"],
            input_digest=request.digest,
        )
        return {"projection": dict(projection), "workspace": str(workspace), "brief": brief}

    def collect_review(
        self, request: AcceptanceReviewRequest, reservation: dict, *, workspace: str, verification_command: str
    ) -> AcceptanceReviewReceipt:
        """Verify the review execution and return the Registry-bound receipt."""
        task_id = str(reservation.get("review_task_id") or "")
        reviewer = str(reservation.get("reviewer_worker_url") or "").rstrip("/")
        current = self.task_lookup(task_id)
        proposal = dict(getattr(current, "last_proposal", None) or {})
        if (
            current is None
            or getattr(current, "status", "") != "completed"
            or getattr(current, "last_exit_code", 1) != 0
            or str(getattr(current, "assigned_agent_url", "") or "").rstrip("/") != reviewer
            or str(proposal.get("command") or "").strip() != verification_command.strip()
        ):
            raise ReviewWorkflowError("worker_review_verification_failed")
        artifact_path = workspace.rstrip("/") + "/.ananta/last-final-answer.json"
        try:
            with open(artifact_path, encoding="utf-8") as stream:
                artifact = json.load(stream)
        except (OSError, ValueError) as exc:
            raise ReviewWorkflowError("worker_review_answer_missing") from exc
        if str(artifact.get("outcome") or "") != "final_answer":
            raise ReviewWorkflowError("worker_review_answer_missing")
        requirements = json.loads(request.requirements_json)
        criteria = requirements.get("acceptance_criteria")
        result = parse_review_verdict(
            str(artifact.get("answer") or ""), criteria_count=len(criteria) if isinstance(criteria, list) else 0
        )
        completed = self.registry.record_result(
            tenant_id=self.tenant_id,
            project_id=PROJECT_ID,
            run_id=reservation["run_id"],
            assignment_id=reservation["assignment_id"],
            dispatch_lease_id=reservation["dispatch_lease_id"],
            terminal_state="succeeded",
            result_digest=canonical_evidence_digest(asdict(result)),
        )
        return AcceptanceReviewReceipt(run=completed, worker_url=reviewer, result=result)
