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

    # ── WFG-008: default-gate insertion ────────────────────────────────

    def insert_default_gates(
        self,
        steps: list[dict],
        *,
        blueprint_name: str,
        settings=None,
    ) -> list[dict]:
        """Insert a default Reviewer gate between Planner and Developer when
        the workflow lacks an explicit gate on the handoff.

        WFG-008: this is the safety net for blueprints that opt into a
        workflow block but don't yet declare explicit gate reviews. The
        default gate keeps the planner-developer handoff auditable and
        ensures a human/auto checkpoint exists before code is written.

        Idempotency rules:
          - If a step with task_kind=gate_review already exists with a
            direct dependency from a Planner-class step to a Developer-
            class step, no default gate is added.
          - The default gate is inserted *after* the upstream step and
            *before* the downstream step in topological order by
            rewriting depends_on: the downstream's depends_on switches
            from the upstream step to the new gate, and the gate's
            depends_on is the upstream step.
          - The default gate's failure_policy is read from the
            deployment setting (workflow_settings.default_gate_policy);
            a per-blueprint workflow.default_failure_policy overrides.
          - The default gate is suppressed entirely when the deployment
            ANANTA_WORKFLOW_MODE=off (handled by reconcile_steps before
            this is called).
        """
        if not steps:
            return steps

        if settings is None:
            from agent.services.workflow_settings import get_workflow_settings
            settings = get_workflow_settings()

        planner_candidates = {
            "planner", "planning", "plan", "architect", "spec",
        }
        developer_candidates = {
            "developer", "dev", "coding", "implementer", "engineer",
        }

        planner_steps = [
            s for s in steps
            if (s.get("role") or "").strip().lower() in planner_candidates
            and not s.get("gate", False)
        ]
        developer_steps = [
            s for s in steps
            if (s.get("role") or "").strip().lower() in developer_candidates
        ]
        if not planner_steps or not developer_steps:
            return steps

        # Find handoffs: a developer step that depends on a planner step
        # directly and has no gate between them.
        planner_ids = {s["id"] for s in planner_steps}
        developer_ids = {s["id"] for s in developer_steps}
        existing_gate_ids = {
            s["id"] for s in steps if s.get("gate", False)
        }

        # Build mutable working copy with stable indexes
        out: list[dict] = [dict(s) for s in steps]
        by_id: dict[str, dict] = {s["id"]: s for s in out}
        inserted: list[tuple[dict, list[str]]] = []  # (gate, planner_deps_rewired)

        for dev in developer_steps:
            deps = list(dev.get("depends_on") or [])
            # Handoff is when a developer step depends directly on a
            # planner step and there is no gate already in the chain.
            direct_planner_deps = [d for d in deps if d in planner_ids]
            if not direct_planner_deps:
                continue
            if any(d in existing_gate_ids for d in deps):
                continue
            if any(d in existing_gate_ids for d in direct_planner_deps):
                continue

            new_gate_id = self._next_default_gate_id(by_id)
            default_failure_policy = self._resolve_default_failure_policy(
                steps, settings
            )
            new_gate: dict = {
                "id": new_gate_id,
                "role": "Reviewer",
                "task_kind": "gate_review",
                "gate": True,
                "checks": {
                    "min_artifacts": [],
                    "min_role": "Planner",
                    "policy": "wfg008_default_planner_to_developer",
                },
                "depends_on": list(direct_planner_deps),
                "failure_policy": default_failure_policy,
                "required_capabilities": [],
                "sort_order": self._max_sort_order(out) + 1,
            }
            inserted.append((new_gate, direct_planner_deps))

        if not inserted:
            return out

        for gate, planner_deps_for_this_gate in inserted:
            out.append(gate)
            by_id[gate["id"]] = gate
            existing_gate_ids.add(gate["id"])
            # Rewire developer steps that pointed at any of the planner
            # step ids this gate now sits behind. We iterate the live
            # `out` list so changes to developer step depends_on are
            # picked up by the topological sort at the end.
            for step in out:
                # Skip the gate we just inserted (its depends_on is the
                # planner step, not the gate itself).
                if step["id"] == gate["id"]:
                    continue
                if step.get("gate", False):
                    # Skip other existing gates — their depends_on was
                    # set by the blueprint author and is not ours to
                    # rewrite.
                    continue
                deps = list(step.get("depends_on") or [])
                if not deps or not any(d in planner_deps_for_this_gate for d in deps):
                    continue
                rewired = [
                    gate["id"] if d in planner_deps_for_this_gate else d
                    for d in deps
                ]
                step["depends_on"] = rewired

        # Renumber sort_order to keep the contract that sort_order is
        # the source of truth for the catalog-side ordering.
        out.sort(key=lambda s: s.get("sort_order", 0))
        return out

    @staticmethod
    def _next_default_gate_id(by_id: dict[str, dict]) -> str:
        """Return a unique default-gate id, suffixed with an integer."""
        n = 1
        while f"wfg008_default_gate_{n}" in by_id:
            n += 1
        return f"wfg008_default_gate_{n}"

    @staticmethod
    def _max_sort_order(steps: list[dict]) -> int:
        return max((int(s.get("sort_order", 0)) for s in steps), default=0)

    @staticmethod
    def _resolve_default_failure_policy(steps: list[dict], settings) -> str:
        """Per-blueprint workflow.default_failure_policy overrides the
        deployment-level setting; otherwise the deployment setting
        applies. The catalog normalizer has already validated the
        blueprint's value, so we trust it here."""
        # The settings dataclass is a frozen WorkflowSettings;
        # default_gate_policy is a GateFailurePolicy enum.
        for step in steps:
            policy = step.get("failure_policy")
            if policy:
                return str(policy)
        return settings.default_gate_policy.value
