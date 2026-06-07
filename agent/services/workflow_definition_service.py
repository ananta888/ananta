"""Workflow definition service (WFG-004).

Owns the materialization of a validated blueprint's workflow block
into BlueprintWorkflowStepDB rows at seed-reconciliation time.

Contract (mirrors docs/decisions/ADR-workflow-gates-blueprint-contract.md):

  - The catalog normalizer
    (agent/services/seed_blueprint_catalog.py::SeedBlueprintCatalog
     ._normalize_workflow) is the authoritative source of workflow
    validity. A blueprint whose workflow block fails validation MUST
    NOT reach this service.
  - This service is a *materializer*, not a validator. It assumes
    the workflow block has already been validated (DAG acyclic,
    role references resolved, gate-checks implication satisfied).
  - Idempotent reconciliation: re-running reconcile_steps for the
    same blueprint replaces its step rows, never duplicates.

The service is invoked from
team_blueprint_reconciliation_service.py (the existing
seed-reconciliation entry point) when a blueprint's normalized JSON
contains a non-null workflow block and the deployment
ANANTA_WORKFLOW_MODE allows the workflow to take effect.

Topological order is computed via Kahn's algorithm and exposed via
topological_order() so the planner (WFG-007) does not have to
re-implement the DAG walk.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Iterable, Sequence

from sqlmodel import Session, select

from agent.db_models import BlueprintWorkflowStepDB, TeamBlueprintDB
from agent.services.workflow_settings import get_workflow_settings


log = logging.getLogger(__name__)


class WorkflowDefinitionError(ValueError):
    """Raised when the workflow definition cannot be materialized."""


class WorkflowDefinitionService:
    """Materializes a validated blueprint's workflow block into DB rows.

    The service is stateless w.r.t. a specific session — every public
    method takes the SQLModel Session as an argument. This keeps the
    service compatible with the existing reconciliation patterns in
    team_blueprint_reconciliation_service.py, which already own the
    session lifecycle.
    """

    def reconcile_steps(
        self,
        session: Session,
        blueprint: TeamBlueprintDB,
        workflow_block: dict,
    ) -> list[BlueprintWorkflowStepDB]:
        """Replace blueprint.workflow steps in DB with the validated block.

        Idempotent: re-running this method for the same blueprint yields
        the same DB state. Existing rows for the blueprint are deleted
        first; new rows are inserted in topological order.

        Raises WorkflowDefinitionError on a malformed block. The block
        is expected to have been validated by the catalog normalizer;
        this is a defense-in-depth check.
        """
        settings = get_workflow_settings()
        if not settings.workflow_block_respected():
            # Deployment-wide kill switch: drop existing step rows and
            # do not create new ones. This makes ANANTA_WORKFLOW_MODE=off
            # reversible at any time without code changes.
            self._delete_steps(session, blueprint.id)
            return []

        steps = self._extract_steps(workflow_block, blueprint.name)
        self._defensive_dag_check(steps, blueprint.name)

        self._delete_steps(session, blueprint.id)
        rows: list[BlueprintWorkflowStepDB] = []
        for step in steps:
            row = BlueprintWorkflowStepDB(
                blueprint_id=blueprint.id,
                step_id=step["id"],
                role_name=step["role"],
                task_kind=step.get("task_kind", "coding"),
                title=step.get("title"),
                description=step.get("description"),
                sort_order=step.get("sort_order", 0),
                produces=list(step.get("produces", [])),
                consumes=list(step.get("consumes", [])),
                depends_on=list(step.get("depends_on", [])),
                gate=bool(step.get("gate", False)),
                checks=dict(step.get("checks", {})),
                failure_policy=step.get("failure_policy"),
                required_capabilities=list(step.get("required_capabilities", [])),
            )
            session.add(row)
            rows.append(row)
        session.flush()
        log.info(
            "WFG-004 reconciled %d workflow steps for blueprint %s",
            len(rows),
            blueprint.name,
        )
        return rows

    def get_steps(
        self, session: Session, blueprint_id: str
    ) -> list[BlueprintWorkflowStepDB]:
        """Return the workflow steps for a blueprint, sorted by sort_order."""
        stmt = (
            select(BlueprintWorkflowStepDB)
            .where(BlueprintWorkflowStepDB.blueprint_id == blueprint_id)
            .order_by(BlueprintWorkflowStepDB.sort_order)
        )
        return list(session.exec(stmt).all())

    def topological_order(
        self, steps: Sequence[BlueprintWorkflowStepDB]
    ) -> list[BlueprintWorkflowStepDB]:
        """Return steps in topological order, rooted at dependencies-empty.

        Kahn's algorithm; raises WorkflowDefinitionError on a cycle.
        The input is expected to be a DAG; the catalog normalizer
        enforces this at seed time, but a defensive check fires here
        for unit tests and operator-facing diagnostics.
        """
        if not steps:
            return []
        index = {s.step_id: s for s in steps}
        in_degree: dict[str, int] = {s.step_id: 0 for s in steps}
        edges: dict[str, list[str]] = defaultdict(list)
        for step in steps:
            for dep in step.depends_on:
                if dep not in index:
                    raise WorkflowDefinitionError(
                        f"step {step.step_id!r} depends on unknown step {dep!r}"
                    )
                in_degree[step.step_id] += 1
                edges[dep].append(step.step_id)

        queue: deque[str] = deque(
            sid for sid, deg in in_degree.items() if deg == 0
        )
        ordered_ids: list[str] = []
        while queue:
            sid = queue.popleft()
            ordered_ids.append(sid)
            for succ in edges[sid]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if len(ordered_ids) != len(steps):
            raise WorkflowDefinitionError(
                f"workflow DAG has a cycle; reached {len(ordered_ids)} of {len(steps)} steps"
            )
        return [index[sid] for sid in ordered_ids]

    # ── internal helpers ────────────────────────────────────────────

    def _delete_steps(self, session: Session, blueprint_id: str) -> None:
        existing = list(
            session.exec(
                select(BlueprintWorkflowStepDB).where(
                    BlueprintWorkflowStepDB.blueprint_id == blueprint_id
                )
            ).all()
        )
        for row in existing:
            session.delete(row)
        session.flush()

    def _extract_steps(
        self, workflow_block: dict, blueprint_name: str
    ) -> list[dict]:
        steps = workflow_block.get("steps")
        if not isinstance(steps, list) or not steps:
            raise WorkflowDefinitionError(
                f"blueprint {blueprint_name!r}: workflow block has no steps"
            )
        seen_ids: set[str] = set()
        for step in steps:
            sid = step.get("id")
            if not isinstance(sid, str) or not sid:
                raise WorkflowDefinitionError(
                    f"blueprint {blueprint_name!r}: step missing 'id'"
                )
            if sid in seen_ids:
                raise WorkflowDefinitionError(
                    f"blueprint {blueprint_name!r}: duplicate step id {sid!r}"
                )
            seen_ids.add(sid)
            if not isinstance(step.get("role"), str):
                raise WorkflowDefinitionError(
                    f"blueprint {blueprint_name!r}: step {sid!r} missing 'role'"
                )
        return steps

    def _defensive_dag_check(
        self, steps: Iterable[dict], blueprint_name: str
    ) -> None:
        ids = {s["id"] for s in steps}
        for step in steps:
            for dep in step.get("depends_on", []):
                if dep not in ids:
                    raise WorkflowDefinitionError(
                        f"blueprint {blueprint_name!r}: step {step['id']!r} "
                        f"depends on unknown step {dep!r}"
                    )
