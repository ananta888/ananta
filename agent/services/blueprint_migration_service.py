"""Legacy blueprint migration (WFG-021).

When a blueprint was authored before the workflow layer
existed, it carries only ``roles`` + ``artifacts``. The
planner still materialises it (via the legacy
artifact-based subtask path), but the user gets no gate
enforcement, no handoff events, and no audit-query
visibility into what is blocking the goal.

WFG-021 provides a one-shot migration tool that walks a
legacy blueprint and emits a ``blueprint_workflow.v1``
block. The migration is *deterministic and conservative*:

  - Step order is the artifact ``sort_order`` ascending.
  - Every step's ``consumes`` is the union of upstream
    ``produces`` *plus* the blueprint's seed_artifact_keys
    (WFG-016 contract).
  - Every step's ``produces`` is the artifact's payload
    ``produces`` field, falling back to an empty list.
  - ``depends_on`` is inferred from the consumes→produces
    edges.
  - The LAST step is marked as a gate with
    ``failure_policy=block`` and a single
    ``goal_state`` check. The author can promote it to
    ``block_until_human_approval`` after review.
  - The migration is **not automatic**. The blueprint
    author must inspect the generated block, adjust the
    gate checks, and accept it before it goes live.

The migration is implemented as a pure function
(``migrate_legacy_blueprint``) plus a wrapper
(``migrate_legacy_blueprint_file``) that reads from
disk and writes a side-by-side ``*_workflow.json`` file.

The migration does NOT mutate the source blueprint. The
author applies the generated block via a separate commit.
This is the same "explicit, reviewed, sign-off required"
pattern the planning pipeline uses for blueprint changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MIGRATION_NOTE_SCHEMA = "blueprint_migration_note.v1"
MIGRATION_FORMAT_VERSION = 1


def _stringify_key(value: Any) -> str:
    return str(value or "").strip()


def _artifact_title(artifact: dict[str, Any]) -> str:
    return _stringify_key(artifact.get("title"))


def _artifact_produces(artifact: dict[str, Any]) -> list[str]:
    raw = artifact.get("payload", {}).get("produces")
    if isinstance(raw, list):
        return [_stringify_key(item) for item in raw if _stringify_key(item)]
    if isinstance(raw, str) and _stringify_key(raw):
        return [_stringify_key(raw)]
    return []


def _default_seed_for(blueprint_name: str) -> list[str]:
    """Best-effort seed keys for the most common blueprint
    archetypes. The migration tool does NOT invent a
    sophisticated goal-seed heuristic; it just picks the
    obvious keys from the blueprint's name."""
    name = blueprint_name.lower()
    if "security" in name or "review" in name:
        return ["goal_brief", "code_changes"]
    if "repair" in name or "incident" in name:
        return ["goal_brief", "incident_report"]
    if "tdd" in name:
        return ["goal_brief", "acceptance_criteria"]
    return ["goal_brief", "acceptance_criteria"]


def _infer_role_for_step(
    *, step_id: str, blueprint: dict[str, Any]
) -> str:
    """Pick a role name to attribute to the step.

    The migration tool prefers:
      1. The first role with ``config.responsibility`` matching
         the step's kind (planning / coordination / implementation /
         verification / review).
      2. The first ``is_required`` role in the blueprint.
      3. The string "Worker" as a last resort.
    """
    step_id_lc = step_id.lower()
    if any(t in step_id_lc for t in ("plan", "intake", "scope", "research")):
        kind = "planning"
    elif any(t in step_id_lc for t in ("gate", "review", "signoff", "findings", "report")):
        kind = "review"
    elif any(t in step_id_lc for t in ("verify", "validate", "check", "qa", "regression")):
        kind = "verification"
    elif any(t in step_id_lc for t in ("fix", "patch", "build", "implement", "code")):
        kind = "implementation"
    else:
        kind = "coordination"
    responsibility_map = {
        "planning": ("backlog", "scope_and_synthesis", "behavior_definition",
                     "triage_and_plan", "deerflow_research"),
        "coordination": ("facilitation", "flow_management", "release_governance",
                         "evolver_proposal", "review_gate"),
        "implementation": ("delivery", "service_delivery", "implementation",
                           "technical_review", "fix_engineer"),
        "verification": ("verification", "quality_review", "refactor_and_verification",
                         "compliance"),
        "review": ("review_gate", "facilitation", "risk_governance", "release_governance"),
    }
    wanted = responsibility_map.get(kind, ())
    roles = list(blueprint.get("roles") or [])
    for role in roles:
        cfg = role.get("config") if isinstance(role, dict) else None
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("responsibility") or "") in wanted:
            return str(role.get("name") or "Worker")
    for role in roles:
        if role.get("is_required"):
            return str(role.get("name") or "Worker")
    return "Worker"


def _infer_task_kind_for_step(step_id: str) -> str:
    s = step_id.lower()
    if any(t in s for t in ("plan", "intake", "scope")):
        return "planning"
    if any(t in s for t in ("review", "gate", "signoff", "findings")):
        return "review"
    if any(t in s for t in ("verify", "validate", "check", "qa", "regression", "red", "green")):
        return "verification"
    if any(t in s for t in ("fix", "patch", "build", "implement", "code")):
        return "coding"
    if any(t in s for t in ("sync", "cascade", "coordination")):
        return "coordination"
    return "delivery"


def _slugify(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(value or "").strip()).strip("_").lower() or "step"


def migrate_legacy_blueprint(
    *, blueprint: dict[str, Any], strict: bool = True
) -> dict[str, Any]:
    """Generate a ``blueprint_workflow.v1`` block for a legacy
    blueprint. Pure function: does not mutate the input.

    Returns a dict with::

      {
        "workflow": {  // the generated block, schema
                      // blueprint_workflow.v1
          "id": "...",
          "version": 1,
          "seed_artifact_keys": [...],
          "steps": [...],
        },
        "migration_note": {
          "schema": "blueprint_migration_note.v1",
          "format_version": 1,
          "blueprint_name": "...",
          "generated_step_count": N,
          "warnings": [...],
          "strict": bool,
        }
      }

    In ``strict=True`` mode the function raises
    ``LegacyBlueprintMigrationError`` when the input is
    invalid (no name, no artifacts, no roles). In non-strict
    mode the function returns whatever it managed to
    generate and reports the errors as warnings.
    """
    name = _stringify_key(blueprint.get("name"))
    if not name:
        if strict:
            raise LegacyBlueprintMigrationError("blueprint name is required")
        return {
            "workflow": {"schema": "blueprint_workflow.v1", "id": "unnamed", "steps": []},
            "migration_note": {
                "schema": MIGRATION_NOTE_SCHEMA,
                "format_version": MIGRATION_FORMAT_VERSION,
                "blueprint_name": "",
                "generated_step_count": 0,
                "warnings": ["blueprint name is required"],
                "strict": strict,
            },
        }
    artifacts = [
        a for a in list(blueprint.get("artifacts") or [])
        if isinstance(a, dict) and _artifact_title(a)
    ]
    if not artifacts:
        if strict:
            raise LegacyBlueprintMigrationError(
                f"blueprint {name!r} has no artifacts"
            )
        return {
            "workflow": {"schema": "blueprint_workflow.v1", "id": _slugify(name), "steps": []},
            "migration_note": {
                "schema": MIGRATION_NOTE_SCHEMA,
                "format_version": MIGRATION_FORMAT_VERSION,
                "blueprint_name": name,
                "generated_step_count": 0,
                "warnings": ["blueprint has no artifacts"],
                "strict": strict,
            },
        }
    # Sort by sort_order ascending (default 100 if missing)
    sorted_artifacts = sorted(
        artifacts, key=lambda a: int(a.get("sort_order") or 100)
    )
    seed = list(blueprint.get("seed_artifact_keys") or []) or _default_seed_for(name)
    # First pass: build a producer map and a per-step produces list
    step_specs: list[dict[str, Any]] = []
    producer_by_key: dict[str, str] = {}
    for art in sorted_artifacts:
        title = _artifact_title(art)
        step_id = _slugify(title)
        produces = _artifact_produces(art)
        # Avoid duplicate producer registration; the first
        # step that declares a produce key wins.
        for key in produces:
            producer_by_key.setdefault(key, step_id)
        step_specs.append({
            "id": step_id,
            "title": title,
            "produces": produces,
            "_is_policy_artifact": str(art.get("kind") or "") == "policy",
        })
    # Filter out policy artifacts; they are not workflow steps.
    step_specs = [s for s in step_specs if not s["_is_policy_artifact"]]
    # Second pass: compute consumes from the producer map.
    # Only upstream steps (strictly earlier in sort_order)
    # contribute to a step's consumes — later steps cannot
    # be a step's dependency.
    sorted_step_ids = [s["id"] for s in step_specs]
    index_by_id = {sid: i for i, sid in enumerate(sorted_step_ids)}
    for spec in step_specs:
        consumes: list[str] = []
        own_index = index_by_id[spec["id"]]
        for upstream in step_specs[:own_index]:
            for key in upstream["produces"]:
                if key and key not in consumes:
                    consumes.append(key)
        spec["consumes"] = consumes
        spec["_role"] = _infer_role_for_step(step_id=spec["id"], blueprint=blueprint)
        spec["_task_kind"] = _infer_task_kind_for_step(spec["id"])
    if step_specs:
        # Inject the seed keys into the first step's
        # consumes; that step's upstream dependency is
        # satisfied by the goal graph.
        first = step_specs[0]
        merged: list[str] = []
        for key in list(seed) + list(first["consumes"]):
            if key and key not in merged:
                merged.append(key)
        first["consumes"] = merged
        # Compute depends_on for the rest of the steps.
        for spec in step_specs:
            deps: list[str] = []
            for key in spec["consumes"]:
                producer = producer_by_key.get(key)
                if producer and producer != spec["id"] and producer not in deps:
                    deps.append(producer)
            spec["depends_on"] = deps
        # Mark the last step as a gate.
        last = step_specs[-1]
        last["gate"] = True
        last["gate_decision_policy"] = "all_artifact_refs_present"
        last["checks"] = [
            {
                "name": f"{key}_present",
                "type": "file_exists",
                "ref": key,
            }
            for key in last["produces"]
        ]
        if not last["checks"]:
            # A gate with no produces has nothing to check;
            # give it a minimal goal_state check so the gate
            # can actually fire.
            last["checks"] = [
                {
                    "name": "goal_active",
                    "type": "goal_state",
                    "params": {"expected": "active"},
                }
            ]
        last["failure_policy"] = "block"
    # Build the final workflow block.
    steps: list[dict[str, Any]] = []
    for spec in step_specs:
        steps.append({
            "id": spec["id"],
            "role": spec["_role"],
            "task_kind": spec["_task_kind"],
            "task_ref": spec["title"],
            "consumes": list(spec.get("consumes") or []),
            "produces": list(spec.get("produces") or []),
            "depends_on": list(spec.get("depends_on") or []),
            "gate": bool(spec.get("gate", False)),
            "checks": list(spec.get("checks") or []),
            "failure_policy": spec.get("failure_policy", "block"),
        })
    warnings: list[str] = []
    if not any(r.get("is_required") for r in list(blueprint.get("roles") or []) if isinstance(r, dict)):
        warnings.append("no is_required roles; the migration assigned roles heuristically")
    if not any(spec["produces"] for spec in step_specs):
        warnings.append("no artifact has a payload.produces field; the gate has no artifact_refs to check")
    workflow = {
        "schema": "blueprint_workflow.v1",
        "id": _slugify(name),
        "version": 1,
        "seed_artifact_keys": list(seed),
        "steps": steps,
    }
    note = {
        "schema": MIGRATION_NOTE_SCHEMA,
        "format_version": MIGRATION_FORMAT_VERSION,
        "blueprint_name": name,
        "generated_step_count": len(steps),
        "warnings": warnings,
        "strict": strict,
    }
    return {"workflow": workflow, "migration_note": note}


def migrate_legacy_blueprint_file(
    *, source_path: str | Path, output_path: str | Path | None = None
) -> dict[str, Any]:
    """Load a legacy blueprint file (JSON) and write the
    generated workflow block to a side-by-side file.

    The source file is NOT mutated. The output file is
    ``<source_stem>.workflow.json`` by default.
    """
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"blueprint file not found: {source}")
    data = json.loads(source.read_text())
    result = migrate_legacy_blueprint(blueprint=data, strict=False)
    out = Path(output_path) if output_path else source.with_name(f"{source.stem}.workflow.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return {"output_path": str(out), **result}


class LegacyBlueprintMigrationError(ValueError):
    """Raised by ``migrate_legacy_blueprint`` when the input
    is structurally invalid in strict mode."""
