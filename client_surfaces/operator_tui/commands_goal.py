from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from agent.artifacts.artifact_access_policy import ArtifactAccessPolicy
from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from client_surfaces.operator_tui.goal_artifact_filters import filter_goal_artifact_view, normalize_goal_artifact_filters
from client_surfaces.operator_tui.models import CommandResult, OperatorState, PanelState


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

def _active_goal_id(state: OperatorState) -> str:
    game = dict(state.header_logo_game or {})
    return str(game.get("active_goal_id") or "").strip()


def _require_active_goal(state: OperatorState) -> tuple[str | None, CommandResult | None]:
    goal_id = _active_goal_id(state)
    if goal_id:
        return goal_id, None
    return None, CommandResult(state, "goal command requires active goal (:goal use <goal-id>)", handled=False)


def _load_goal_artifact_payload(*, state: OperatorState, goal_id: str) -> dict:
    service = GoalArtifactService()
    graph = service.get_goal_graph(goal_id)
    provenance_items = list(dict(graph.get("extensions") or {}).get("execution_provenance") or [])
    provenance_by_id = {
        str(item.get("provenance_id") or ""): item
        for item in provenance_items
        if isinstance(item, dict) and str(item.get("provenance_id") or "").strip()
    }
    outputs = []
    for row in list(graph.get("output_artifacts") or []):
        item = dict(row) if isinstance(row, dict) else {}
        provenance = provenance_by_id.get(str(item.get("provenance_id") or ""), {})
        prompt_refs = dict(provenance.get("prompt_refs") or {})
        runtime_ref = dict(provenance.get("runtime_target_ref") or {})
        model_ref = dict(provenance.get("model_ref") or {})
        item["prompt_template_ref"] = str(prompt_refs.get("prompt_template_ref") or "")
        item["model_ref"] = str(model_ref.get("model_id") or "")
        item["runtime_ref"] = str(runtime_ref.get("runtime_type") or "")
        item["execution_summary"] = (
            f"task={item.get('task_id') or '-'} worker={item.get('worker_id') or '-'} "
            f"runtime={item.get('runtime_ref') or '-'} model={item.get('model_ref') or '-'} "
            f"prompt={item.get('prompt_template_ref') or '-'}"
        )
        outputs.append(item)
    filters = normalize_goal_artifact_filters(dict((state.header_logo_game or {}).get("goal_artifact_filters") or {}))
    filtered = filter_goal_artifact_view(
        source_grants=list(graph.get("source_grants") or []),
        source_usages=list(graph.get("source_usages") or []),
        output_artifacts=outputs,
        filters=filters,
    )
    return {
        "goal_artifacts_mode": True,
        "goal_id": goal_id,
        "filters": filters,
        **filtered,
    }



def handle_goal_command(args: list[str], state: OperatorState) -> CommandResult:
    if not args:
        return CommandResult(state, "goal: use <goal-id> | artifacts [filter ...|clear-filter] | sources candidates", handled=False)
    action = str(args[0]).lower()
    game = dict(state.header_logo_game or {})
    service = GoalArtifactService()
    if action == "use":
        if len(args) < 2:
            return CommandResult(state, "goal use <goal-id>", handled=False)
        goal_id = str(args[1]).strip()
        if not goal_id:
            return CommandResult(state, "goal use <goal-id>", handled=False)
        game["active_goal_id"] = goal_id
        payload = _load_goal_artifact_payload(state=state.with_updates(header_logo_game=game), goal_id=goal_id)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"active goal {goal_id}",
            ),
            f"goal {goal_id} active",
        )
    goal_id, error = _require_active_goal(state)
    if error is not None or goal_id is None:
        return error or CommandResult(state, "active goal required", handled=False)
    if action == "artifacts":
        filters = dict(game.get("goal_artifact_filters") or {})
        if len(args) >= 2 and str(args[1]).lower() == "clear-filter":
            filters = {}
            game["goal_artifact_filters"] = {}
        elif len(args) >= 2 and str(args[1]).lower() == "filter":
            for token in args[2:]:
                text = str(token).strip()
                if "=" not in text:
                    continue
                key, value = text.split("=", 1)
                if key.strip() in {"source_id", "artifact_type", "sensitivity", "status", "worker_id", "task_id", "prompt_template_ref", "model_ref"}:
                    if value.strip():
                        filters[key.strip()] = value.strip()
            game["goal_artifact_filters"] = normalize_goal_artifact_filters(filters)
        payload = _load_goal_artifact_payload(state=state.with_updates(header_logo_game=game), goal_id=goal_id)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        active_filters = payload.get("filters") or {}
        filter_label = ", ".join(f"{k}={v}" for k, v in active_filters.items()) if active_filters else "none"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"goal artifacts {goal_id} filters={filter_label}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if action == "sources":
        if len(args) < 2:
            return CommandResult(state, "goal sources candidates", handled=False)
        sub = str(args[1]).lower()
        if sub != "candidates":
            return CommandResult(state, "goal sources candidates", handled=False)
        rows = ArtifactCandidateService(goal_artifact_service=service).list_candidates(goal_id=goal_id)
        return CommandResult(
            state.with_updates(status_message=f"goal sources candidates {goal_id}: {len(rows)}"),
            json.dumps({"goal_id": goal_id, "candidates": rows}, ensure_ascii=False),
        )
    if action == "source":
        if len(args) < 2:
            return CommandResult(state, "goal source grant|revoke|detail ...", handled=False)
        sub = str(args[1]).lower()
        if sub == "grant":
            if len(args) < 3:
                return CommandResult(state, "goal source grant <artifact-ref> --usage use_as_context", handled=False)
            artifact_ref = str(args[2]).strip()
            usage = "use_as_context"
            for idx, token in enumerate(args[3:], start=3):
                if str(token).lower() == "--usage" and idx + 1 < len(args):
                    usage = str(args[idx + 1]).strip()
            policy = ArtifactAccessPolicy().evaluate(
                goal_id=goal_id,
                artifact_sensitivity="internal",
                requested_usage=usage,
                worker_kind="general",
                provider_location="local",
                data_boundary="project_private",
            )
            if policy.decision != "allow":
                return CommandResult(state, f"grant denied reason={policy.reason_code}", handled=False)
            grant_id = f"grant-{hashlib.sha1(f'{goal_id}:{artifact_ref}:{usage}'.encode('utf-8')).hexdigest()[:10]}"
            grant_payload = {
                "schema": "source_artifact_grant.v1",
                "grant_id": grant_id,
                "goal_id": goal_id,
                "artifact_ref": artifact_ref,
                "granted_by": "operator_tui",
                "granted_at": _now_iso(),
                "allowed_usages": sorted(set(["read", usage])),
                "data_boundary": "project_private",
                "sensitivity": "internal",
                "policy_decision_ref": policy.policy_decision_ref,
            }
            try:
                created = service.create_grant(goal_id=goal_id, grant=grant_payload)
            except GoalArtifactServiceError as exc:
                return CommandResult(state, f"grant failed reason={exc.reason_code}", handled=False)
            return CommandResult(
                state.with_updates(status_message=f"goal source granted {grant_id}"),
                json.dumps(created, ensure_ascii=False),
            )
        if sub == "revoke":
            if len(args) < 3:
                return CommandResult(state, "goal source revoke <grant-id>", handled=False)
            grant_id = str(args[2]).strip()
            try:
                revoked = service.revoke_grant(goal_id=goal_id, grant_id=grant_id, revoke_reason="operator_tui_revoke")
            except GoalArtifactServiceError as exc:
                return CommandResult(state, f"revoke failed reason={exc.reason_code}", handled=False)
            return CommandResult(
                state.with_updates(status_message=f"goal source revoked {grant_id}"),
                json.dumps(revoked, ensure_ascii=False),
            )
        if sub == "detail":
            if len(args) < 3:
                return CommandResult(state, "goal source detail <grant-id>", handled=False)
            grant_id = str(args[2]).strip()
            graph = service.get_goal_graph(goal_id)
            for grant in list(graph.get("source_grants") or []):
                if str(grant.get("grant_id") or "") != grant_id:
                    continue
                detail = {
                    "grant_id": grant_id,
                    "artifact_ref": grant.get("artifact_ref"),
                    "data_boundary": grant.get("data_boundary"),
                    "sensitivity": grant.get("sensitivity"),
                    "allowed_usages": grant.get("allowed_usages"),
                    "policy_decision_ref": grant.get("policy_decision_ref"),
                    "expires_at": grant.get("expires_at"),
                    "revoked_at": grant.get("revoked_at"),
                }
                return CommandResult(state.with_updates(status_message=f"goal source detail {grant_id}"), json.dumps(detail, ensure_ascii=False))
            return CommandResult(state, f"grant not found: {grant_id}", handled=False)
        return CommandResult(state, "goal source grant|revoke|detail ...", handled=False)
    return CommandResult(state, "goal: use <goal-id> | artifacts [filter ...|clear-filter] | sources candidates", handled=False)


def handle_artifact_command(args: list[str], state: OperatorState) -> CommandResult:
    if len(args) < 2:
        return CommandResult(state, "artifact provenance|prompt|config <output-artifact-id>", handled=False)
    action = str(args[0]).lower()
    if action not in {"provenance", "prompt", "config"}:
        return CommandResult(state, "artifact provenance|prompt|config <output-artifact-id>", handled=False)
    goal_id, error = _require_active_goal(state)
    if error is not None or goal_id is None:
        return error or CommandResult(state, "active goal required", handled=False)
    output_id = str(args[1]).strip()
    service = GoalArtifactService()
    graph = service.get_goal_graph(goal_id)
    outputs = list(graph.get("output_artifacts") or [])
    output = next((row for row in outputs if str(row.get("output_artifact_id") or "") == output_id), None)
    if output is None:
        return CommandResult(state, f"output artifact not found: {output_id}", handled=False)
    provenance_id = str(output.get("provenance_id") or "")
    provenance = service.get_execution_provenance(goal_id=goal_id, provenance_id=provenance_id) if provenance_id else None
    if action == "prompt":
        prompt_refs = dict((provenance or {}).get("prompt_refs") or {})
        detail = {
            "output_artifact_id": output_id,
            "provenance_id": provenance_id or None,
            "prompt_template_ref": prompt_refs.get("prompt_template_ref"),
            "prompt_template_version": prompt_refs.get("prompt_template_version"),
            "prompt_template_hash": prompt_refs.get("prompt_template_hash"),
            "variables_hash": prompt_refs.get("prompt_variables_hash"),
            "final_prompt_hash": prompt_refs.get("final_prompt_hash"),
            "raw_prompt_status": "raw prompt not stored"
            if not bool(prompt_refs.get("raw_prompt_stored"))
            else "raw prompt stored",
            "reason_code": prompt_refs.get("reason_code") if str(prompt_refs.get("reason_code") or "").strip() else "",
        }
        return CommandResult(
            state.with_updates(status_message=f"artifact prompt {output_id}"),
            json.dumps(detail, ensure_ascii=False),
        )
    if action == "config":
        config_refs = dict((provenance or {}).get("config_refs") or {})
        detail = {
            "output_artifact_id": output_id,
            "provenance_id": provenance_id or None,
            "worker_config_ref": config_refs.get("worker_config_ref"),
            "runtime_config_ref": config_refs.get("runtime_config_ref"),
            "model_config_ref": config_refs.get("model_config_ref"),
            "policy_config_ref": config_refs.get("policy_config_ref"),
        }
        return CommandResult(
            state.with_updates(status_message=f"artifact config {output_id}"),
            json.dumps(detail, ensure_ascii=False),
        )
    usages = list(graph.get("source_usages") or [])
    grants_by_id = {str(row.get("grant_id") or ""): row for row in list(graph.get("source_grants") or [])}
    usage_rows = [row for row in usages if str(row.get("usage_id") or "") in set(list(output.get("input_usage_refs") or []))]
    sources: list[dict[str, object]] = []
    for row in usage_rows:
        grant = grants_by_id.get(str(row.get("grant_id") or ""), {})
        revoked_after_use = bool(grant and grant.get("revoked_at"))
        sources.append(
            {
                "usage_id": row.get("usage_id"),
                "artifact_ref": row.get("artifact_ref"),
                "grant_id": row.get("grant_id"),
                "revoked_after_use": revoked_after_use,
                "source_reference": row.get("source_reference"),
            }
        )
    detail = {
        "output_artifact_id": output.get("output_artifact_id"),
        "goal_id": output.get("goal_id"),
        "task_id": output.get("task_id"),
        "worker_id": output.get("worker_id"),
        "worker_kind": (provenance or {}).get("worker_kind"),
        "runtime_target_ref": (provenance or {}).get("runtime_target_ref"),
        "model_ref": (provenance or {}).get("model_ref"),
        "config_refs": (provenance or {}).get("config_refs"),
        "prompt_refs": (provenance or {}).get("prompt_refs"),
        "provenance_id": provenance_id or None,
        "execution_id": output.get("execution_id"),
        "content_hash": output.get("content_hash"),
        "input_usage_refs": output.get("input_usage_refs") or [],
        "output_artifact_refs": list((provenance or {}).get("output_artifact_refs") or []),
        "sources": sources,
        "note": "no input artifacts recorded" if not usage_rows else "",
    }
    return CommandResult(
        state.with_updates(status_message=f"artifact provenance {output_id}"),
        json.dumps(detail, ensure_ascii=False),
    )
