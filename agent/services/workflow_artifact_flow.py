"""Workflow artifact-flow enforcement (WFG-016).

A blueprint's workflow DAG has two artifact axes:

  - produces: the artifact KEYS a step writes
  - consumes: the artifact KEYS a step must see before it may run

WFG-016 makes the consumes axis load-bearing:

  1. validate_workflow_artifact_graph(steps) — runs at workflow
     load time (WFG-001/WFG-004). Detects:
       - steps whose consumes include keys that NO upstream step
         produces and that are not in the goal's seed artifacts
       - steps that produce duplicate keys with conflicting types
     Returns a list of violations; the caller refuses to materialise
     the workflow if any are reported.

  2. resolve_required_artifact_refs(step, task, step_task_outputs) —
     what artifact KEYS a worker is allowed to see for a step.
     Strict-allowlist: only the consumes union is exposed. The
     worker_execution_context['allowed_artifact_refs'] carries the
     list. A step that has no consumes gets an empty allowlist
     (deny-by-default), but legacy / direct workflows without
     consumes keep seeing the full set (backward compat).

  3. evaluate_artifact_blocker(step, task, step_task_outputs,
     goal_seed_artifact_keys) — fast-path "can this task start?".
     Returns a dict with keys:
       {
         "blocked": bool,
         "missing_consumes": [...],
         "reason_code": "missing_artifacts" | "ok" | "...",
       }
     The queue uses this to mark a materialised but unsatisfiable
     step as ``blocked_with_missing_artifacts`` without claiming
     it.

  4. filter_worker_artifact_refs(step, candidate_refs) — the
     deny-by-default filter for the worker's read view. Pure
     function: take the candidate list, return the subset the
     step is allowed to see.

Gating:

  - The check is enabled when
    ``ANANTA_WORKFLOW_ARTIFACT_FLOW=1`` (default; see
    ``workflow_settings.py``). Off-mode short-circuits to "ok"
    so legacy blueprints continue to work.
  - Steps with ``consumes=[]`` or absent are NOT subject to
    enforcement (they opt out). This keeps direct-handoff and
    legacy blueprints that have no artifact contract working.

The module is pure: no I/O, no LLM. It is consumed by
``planning_track_task_integration_service`` and the gate engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


WORKFLOW_ARTIFACT_GRAPH_SCHEMA = "workflow_artifact_graph.v1"

# A step's consumes list can be supplied in three shapes:
#   - list[str]            (keys)
#   - list[dict]           ({"key": "execution_plan"} or {"key": "...", "type": "..."})
#   - dict (key -> {type, optional})
# The normaliser flattens to a list[str] + list[(key, type)].
NORMALIZED_KEY = "key"
NORMALIZED_TYPE = "type"
NORMALIZED_OPTIONAL = "optional"


@dataclass(frozen=True)
class ArtifactRef:
    """A single artifact reference inside a consumes/produces list."""

    key: str
    type: str = ""
    optional: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return {"key": self.key, "type": self.type, "optional": self.optional}


@dataclass(frozen=True)
class ArtifactFlowViolation:
    """A single violation surfaced by validate_workflow_artifact_graph."""

    step_id: str
    missing_key: str
    reason: str  # "no_producer" | "duplicate_key_with_conflicting_type" | ...

    def to_dict(self) -> dict[str, str]:
        return {
            "step_id": self.step_id,
            "missing_key": self.missing_key,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ArtifactFlowReport:
    """Result of validate_workflow_artifact_graph."""

    violations: tuple[ArtifactFlowViolation, ...] = ()
    producer_by_key: dict[str, str] = field(default_factory=dict)
    producer_type_by_key: dict[str, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_ARTIFACT_GRAPH_SCHEMA,
            "is_valid": self.is_valid,
            "violations": [v.to_dict() for v in self.violations],
            "producer_by_key": dict(self.producer_by_key),
        }


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------


def _normalize_artifact_list(raw: Any) -> list[ArtifactRef]:
    """Coerce ``raw`` into a list of ``ArtifactRef``.

    Accepts:
      - None / empty -> []
      - list[str]
      - list[dict]   with key, optional type/optional
      - dict         {key -> {type, optional} | type string | True}
    Whitespace-only keys are dropped; duplicate keys are deduped
    with the FIRST occurrence winning (so a step's "preferred type"
    is preserved).
    """
    if not raw:
        return []
    out: list[ArtifactRef] = []
    seen: set[str] = set()
    if isinstance(raw, dict):
        iterable = list(raw.items())
    elif isinstance(raw, list):
        iterable = []
        for item in raw:
            if isinstance(item, str):
                iterable.append((item, None))
            elif isinstance(item, dict):
                key = item.get("key") or item.get("id") or ""
                iterable.append((key, item))
    else:
        return []
    for key, info in iterable:
        key = str(key or "").strip()
        if not key or key in seen:
            continue
        type_ = ""
        optional = False
        if isinstance(info, dict):
            type_ = str(info.get("type") or info.get("artifact_type") or "").strip()
            opt = info.get("optional", False)
            optional = bool(opt) if isinstance(opt, bool) else (
                str(opt).strip().lower() in {"1", "true", "yes", "on"}
            )
        elif isinstance(info, str):
            type_ = info.strip()
        elif isinstance(info, bool):
            # {"key": True} -> optional consume
            optional = bool(info)
        seen.add(key)
        out.append(ArtifactRef(key=key, type=type_, optional=optional))
    return out


def step_consumes(step: dict | None) -> list[ArtifactRef]:
    """Read a step's consumes list, normalized."""
    if not isinstance(step, dict):
        return []
    return _normalize_artifact_list(step.get("consumes"))


def step_produces(step: dict | None) -> list[ArtifactRef]:
    """Read a step's produces list, normalized."""
    if not isinstance(step, dict):
        return []
    return _normalize_artifact_list(step.get("produces"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_workflow_artifact_graph(
    *,
    steps: list[dict[str, Any]],
    goal_seed_artifact_keys: list[str] | tuple[str, ...] | None = None,
) -> ArtifactFlowReport:
    """Walk the DAG and report consumes / produces violations.

    Topological order is NOT required. A step is allowed to consume
    any artifact that either (a) is in ``goal_seed_artifact_keys``
    (these are the inputs the goal itself ships with) or (b) is
    produced by ANY earlier step in the workflow.

    "Earlier" is approximated by declaration order here; the
    planner materializer is responsible for topologically ordering
    the steps before this call. A cyclic declaration is detected
    by the workflow definition service (WFG-004) BEFORE we get
    here, so we do not re-detect cycles in this layer.

    Duplicate keys with conflicting types surface as violations
    (a downstream type-narrowing gate would otherwise have to
    guess which producer to trust).
    """
    if not isinstance(steps, list):
        return ArtifactFlowReport(violations=())
    seed_keys = {
        str(k).strip() for k in list(goal_seed_artifact_keys or []) if str(k).strip()
    }
    producer_by_key: dict[str, str] = {}
    producer_type_by_key: dict[str, str] = {}
    type_conflict_by_key: dict[str, str] = {}
    violations: list[ArtifactFlowViolation] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            continue
        # Register producers first so an early self-consuming step
        # sees its own produce (it shouldn't, but the conservative
        # behavior is "no violation"; the planner catches cycles).
        for ref in step_produces(step):
            if ref.key in producer_by_key and producer_by_key[ref.key] != step_id:
                if ref.type and producer_type_by_key.get(ref.key) and ref.type != producer_type_by_key[ref.key]:
                    type_conflict_by_key[ref.key] = ref.type
            producer_by_key.setdefault(ref.key, step_id)
            if ref.type:
                producer_type_by_key.setdefault(ref.key, ref.type)
        # Then check consumes
        for ref in step_consumes(step):
            if ref.key in seed_keys:
                continue
            if ref.key in producer_by_key:
                continue
            if ref.optional:
                # Optional consumes may go unsatisfied; the
                # blocker evaluator (evaluate_artifact_blocker)
                # surfaces the truth at queue-claim time.
                continue
            violations.append(
                ArtifactFlowViolation(
                    step_id=step_id,
                    missing_key=ref.key,
                    reason="no_producer",
                )
            )
    # Surface type conflicts as additional violations
    for key, conflict_type in type_conflict_by_key.items():
        first_producer = producer_by_key.get(key, "<unknown>")
        violations.append(
            ArtifactFlowViolation(
                step_id=first_producer,
                missing_key=key,
                reason=f"duplicate_key_with_conflicting_type:{conflict_type}",
            )
        )
    return ArtifactFlowReport(
        violations=tuple(violations),
        producer_by_key=dict(producer_by_key),
        producer_type_by_key=dict(producer_type_by_key),
    )


# ---------------------------------------------------------------------------
# Per-step resolution
# ---------------------------------------------------------------------------


def resolve_required_artifact_refs(
    *,
    step: dict | None,
    goal_seed_artifact_keys: list[str] | tuple[str, ...] | None = None,
) -> list[ArtifactRef]:
    """Compute the artifact allowlist for a step.

    Strict-allowlist semantics:

      - step has explicit consumes -> that allowlist (plus the
        goal seed keys, which are always available)
      - step has empty consumes and no explicit empty marker ->
        the goal seed keys (conservative default; deny extra refs)
      - step has consumes=[] explicitly -> the goal seed keys only
        (the author chose to opt out of any produces flow)

    The function does NOT consider produced-by-upstream filtering
    because that depends on the executor's view of the goal graph,
    not on the workflow definition.
    """
    if not isinstance(step, dict):
        return []
    consumes = step_consumes(step)
    seed_keys = {
        str(k).strip() for k in list(goal_seed_artifact_keys or []) if str(k).strip()
    }
    if not consumes:
        # No consumes declared: the conservative allowlist is
        # "goal seed only". The author can opt out by setting
        # consumes=[] explicitly with no goal seed (== deny all).
        return [ArtifactRef(key=k) for k in sorted(seed_keys)]
    refs = [r for r in consumes if not r.optional or r.key in seed_keys]
    # Add seeds not already in the list
    seed_refs = [ArtifactRef(key=k) for k in sorted(seed_keys) if k not in {r.key for r in refs}]
    return refs + seed_refs


def filter_worker_artifact_refs(
    *,
    step: dict | None,
    candidate_refs: list[str] | tuple[str, ...] | None,
    goal_seed_artifact_keys: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Deny-by-default filter: keep only refs the step is allowed to see.

    Pure function. Returns a list (not a tuple) so the worker
    can JSON-serialize the response directly.
    """
    allowed = {
        ref.key
        for ref in resolve_required_artifact_refs(
            step=step, goal_seed_artifact_keys=goal_seed_artifact_keys
        )
    }
    if not candidate_refs:
        return []
    return [str(r) for r in candidate_refs if str(r).strip() in allowed]


# ---------------------------------------------------------------------------
# Blocker evaluation (used by the queue / gate)
# ---------------------------------------------------------------------------


def evaluate_artifact_blocker(
    *,
    step: dict | None,
    produced_artifact_keys: list[str] | tuple[str, ...] | None = None,
    goal_seed_artifact_keys: list[str] | tuple[str, ...] | None = None,
    artifact_flow_enabled: bool = True,
) -> dict[str, Any]:
    """Decide whether a step is blocked because required artifacts
    are not yet produced.

    Returns::

      {
        "blocked": bool,
        "missing_consumes": [ArtifactRef, ...],
        "reason_code": "missing_artifacts" | "ok" | "artifact_flow_disabled" | "no_consumes_declared",
      }

    The caller (queue) writes the result into
    ``status_reason_details.missing_artifacts`` so the audit query
    (WFG-017) can surface it.
    """
    if not artifact_flow_enabled:
        return {
            "blocked": False,
            "missing_consumes": [],
            "reason_code": "artifact_flow_disabled",
        }
    if not isinstance(step, dict):
        return {
            "blocked": False,
            "missing_consumes": [],
            "reason_code": "ok",
        }
    consumes = step_consumes(step)
    if not consumes:
        return {
            "blocked": False,
            "missing_consumes": [],
            "reason_code": "no_consumes_declared",
        }
    produced = {
        str(k).strip() for k in list(produced_artifact_keys or []) if str(k).strip()
    }
    seeds = {
        str(k).strip() for k in list(goal_seed_artifact_keys or []) if str(k).strip()
    }
    missing = [
        ref for ref in consumes
        if ref.key not in produced and ref.key not in seeds and not ref.optional
    ]
    return {
        "blocked": bool(missing),
        "missing_consumes": [ref.to_dict() for ref in missing],
        "reason_code": "missing_artifacts" if missing else "ok",
    }
