"""Compact Organization management commands backed exclusively by Hub APIs."""

from __future__ import annotations

import json
import os
import secrets
from typing import Any
from urllib.parse import quote, urlencode

from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState, PanelState
from client_surfaces.operator_tui.ops_api_client import OpsApiClient, OpsApiHttpError


def handle_organization_command(args: list[str], state: OperatorState) -> CommandResult:
    """Handle ``:org`` without reading Hub persistence or addressing workers."""

    action = str(args[0] if args else "status").lower()
    if action not in {
        "status",
        "list",
        "blueprints",
        "show",
        "planning",
        "proposals",
        "proposal",
        "validate",
        "instantiate",
        "lifecycle",
        "export",
        "help",
    }:
        return _usage(state)
    if action == "help":
        return _usage(state)

    client = OpsApiClient(state.endpoint, token=_token_from_state(state))
    options, positional = _parse_options(args[1:])
    try:
        if action == "blueprints":
            payload = _payload(
                "blueprints", _unwrap(client.request_json("GET", "/api/organization-blueprints?page_size=100"))
            )
        elif action in {"status", "list"}:
            organizations = _unwrap(client.request_json("GET", "/api/organizations?page_size=100"))
            blueprints = (
                _unwrap(client.request_json("GET", "/api/organization-blueprints?page_size=100"))
                if action == "status"
                else {}
            )
            payload = _payload("status", {"organizations": _items(organizations), "blueprints": _items(blueprints)})
        elif action == "show":
            organization_id = _required_id(positional, state, "org show <organization-id>")
            if isinstance(organization_id, CommandResult):
                return organization_id
            query = urlencode(
                {
                    "page_size": _bounded_int(options.get("page-size"), default=100, minimum=1, maximum=100),
                    "depth": _bounded_int(options.get("depth"), default=3, minimum=1, maximum=16),
                    "include_runtime": "true",
                }
            )
            topology = _unwrap(
                client.request_json("GET", f"/api/organizations/{quote(organization_id, safe='')}/topology?{query}")
            )
            payload = _payload("topology", {"organization_id": organization_id, "topology": topology})
        elif action in {"planning", "proposals"}:
            organization_id = _required_id(positional, state, f"org {action} <organization-id>")
            if isinstance(organization_id, CommandResult):
                return organization_id
            planning = _unwrap(
                client.request_json("GET", f"/api/organizations/{quote(organization_id, safe='')}/planning")
            )
            if action == "proposals":
                planning = {"organization_id": organization_id, "proposals": list(planning.get("proposals") or [])}
            payload = _payload(action, planning)
        elif action == "proposal":
            return _decide_proposal(client, positional, options, state)
        elif action == "validate":
            if not positional:
                return CommandResult(
                    state,
                    "org validate <blueprint-key> "
                    "[--teams 5..10 | --composition key=count,... --reason <text>] "
                    "[--title <name>]",
                    handled=False,
                )
            blueprint_key = _safe_key(positional[0])
            title = str(options.get("title") or "Enterprise Produktorganisation")[:120]
            raw_composition = options.get("composition")
            if raw_composition:
                if "teams" in options:
                    raise ValueError("organization_composition_mode_conflict")
                composition = _parse_composition(raw_composition)
                reason = str(options.get("reason") or "").strip()
                if not reason:
                    raise ValueError("organization_admission_reason_required")
                admission = _unwrap(
                    client.request_json(
                        "POST",
                        f"/api/organization-blueprints/{quote(blueprint_key, safe='')}/admission-exceptions",
                        payload={
                            "team_blueprint_counts": composition,
                            "reason": reason,
                            "ttl_seconds": 900,
                        },
                        headers={
                            "Idempotency-Key": f"organization-tui-admission:{secrets.token_urlsafe(18)}",
                        },
                        timeout=30.0,
                    )
                )
                if str(admission.get("status") or "") != "issued":
                    raise ValueError("organization_admission_exception_not_issued")
                request = {
                    "blueprint_key": blueprint_key,
                    "title": title,
                    "parameters": {"team_blueprint_counts": composition},
                    "admission_exception_ref": str(admission.get("admission_exception_ref") or ""),
                }
            else:
                team_count = _bounded_int(options.get("teams"), default=8, minimum=5, maximum=10)
                request = {
                    "blueprint_key": blueprint_key,
                    "title": title,
                    "team_count": team_count,
                }
            plan = _unwrap(
                client.request_json(
                    "POST",
                    f"/api/organization-blueprints/{quote(blueprint_key, safe='')}/compile",
                    payload=request,
                    timeout=30.0,
                )
            )
            game = dict(state.header_logo_game or {})
            game["organization_compile_plan"] = plan
            payload = _payload("compile", _redact_compile_plan(plan))
            state = state.with_updates(header_logo_game=game)
        elif action == "instantiate":
            return _instantiate(client, options, state)
        elif action == "lifecycle":
            return _transition_lifecycle(client, positional, options, state)
        else:  # export
            organization_id = _required_id(positional, state, "org export <organization-id> [--json|--mermaid]")
            if isinstance(organization_id, CommandResult):
                return organization_id
            bundle = _unwrap(
                client.request_json(
                    "GET",
                    f"/api/organization-bundles/export?{urlencode({'organization_id': organization_id})}",
                    timeout=30.0,
                )
            )
            payload = _payload("bundle", {"organization_id": organization_id, "bundle": bundle})
    except (OpsApiHttpError, ValueError) as exc:
        reason = exc.code if isinstance(exc, OpsApiHttpError) else str(exc)
        return CommandResult(
            state.with_updates(status_message=f"organization blocked: {reason}"),
            f"organization blocked: {reason}",
            handled=False,
        )

    output_mode = "mermaid" if "mermaid" in options else "json" if "json" in options else "compact"
    payload["output_mode"] = output_mode
    result_state = _show_payload(state, payload, f"organization {action}")
    if output_mode == "json":
        message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    elif output_mode == "mermaid":
        message = _to_mermaid(payload)
    else:
        message = f"organization {action} loaded"
    return CommandResult(result_state, message)


def _instantiate(client: OpsApiClient, options: dict[str, str | bool], state: OperatorState) -> CommandResult:
    plan = dict(dict(state.header_logo_game or {}).get("organization_compile_plan") or {})
    if not plan:
        return CommandResult(state, "org instantiate requires a prior org validate dry-run", handled=False)
    if "confirm" not in options:
        return CommandResult(
            state.with_updates(status_message="organization instantiate awaits explicit --confirm"),
            "org instantiate --confirm (uses the bound dry-run; no workers or tasks are started)",
            handled=True,
        )
    admin_grant = str(os.environ.get("ANANTA_ORGANIZATION_ADMIN_GRANT") or "").strip()
    if not admin_grant:
        return CommandResult(
            state,
            "ANANTA_ORGANIZATION_ADMIN_GRANT is required; grants are never accepted as command arguments",
            handled=False,
        )
    revision = str(plan.get("definition_revision") or "").strip()
    digest = str(plan.get("plan_digest") or "").strip()
    if not revision or not digest:
        return CommandResult(state, "bound compile plan lacks revision or digest", handled=False)
    title = str(plan.get("title") or "").strip()
    if not title:
        return CommandResult(state, "bound compile plan lacks title", handled=False)
    result = dict(
        _unwrap(
            client.request_json(
                "POST",
                "/api/organizations",
                payload={"compile_plan": plan, "title": title, "admin_grant": admin_grant},
                headers={
                    "If-Match": f'"{revision}"',
                    "Idempotency-Key": f"organization-tui:{secrets.token_urlsafe(18)}",
                    "X-Organization-Admin-Grant": admin_grant,
                    "X-Plan-Digest": digest,
                },
                timeout=30.0,
            )
        )
    )
    game = dict(state.header_logo_game or {})
    game.pop("organization_compile_plan", None)
    issued_grant = str(result.pop("organization_admin_grant_id", "") or "").strip()
    if issued_grant:
        result["organization_admin_grant_issued"] = True
    payload = _payload("instantiate", result)
    return CommandResult(
        _show_payload(state.with_updates(header_logo_game=game), payload, "organization instantiated"),
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _decide_proposal(
    client: OpsApiClient,
    positional: list[str],
    options: dict[str, str | bool],
    state: OperatorState,
) -> CommandResult:
    if len(positional) != 3 or positional[2] not in {"approve", "reject"}:
        return CommandResult(
            state,
            "org proposal <organization-id> <proposal-id> approve|reject --confirm",
            handled=False,
        )
    organization_id = _safe_key(positional[0])
    proposal_id = _safe_key(positional[1])
    operation = positional[2]
    if "confirm" not in options:
        return CommandResult(
            state.with_updates(status_message="proposal decision awaits explicit --confirm"),
            "org proposal decision is dry until --confirm is supplied",
        )
    planning = _unwrap(
        client.request_json(
            "GET",
            f"/api/organizations/{quote(organization_id, safe='')}/planning?page_size=50",
        )
    )
    proposal = next(
        (
            dict(item)
            for item in list(planning.get("proposals") or [])
            if isinstance(item, dict) and str(item.get("proposal_id") or "") == proposal_id
        ),
        None,
    )
    if proposal is None:
        raise ValueError("organization_proposal_not_found")
    raw_revision = proposal.get("revision")
    digest = str(proposal.get("digest") or "").strip()
    if isinstance(raw_revision, bool) or not str(raw_revision or "").isdigit() or not digest:
        raise ValueError("organization_proposal_precondition_missing")
    revision = int(str(raw_revision))
    if revision < 1:
        raise ValueError("organization_proposal_precondition_missing")
    result = _unwrap(
        client.request_json(
            "POST",
            (
                f"/api/organizations/{quote(organization_id, safe='')}/proposals/"
                f"{quote(proposal_id, safe='')}/{operation}"
            ),
            payload={"expected_revision": revision, "expected_digest": digest},
            timeout=30.0,
        )
    )
    payload = _payload("proposal-decision", result)
    return CommandResult(
        _show_payload(state, payload, f"proposal {operation}"),
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _transition_lifecycle(
    client: OpsApiClient,
    positional: list[str],
    options: dict[str, str | bool],
    state: OperatorState,
) -> CommandResult:
    allowed_states = {"draft", "validated", "active", "paused", "completed", "archived"}
    if len(positional) != 2 or positional[1] not in allowed_states:
        return CommandResult(
            state,
            "org lifecycle <organization-id> draft|validated|active|paused|completed|archived "
            "[--strategy drain|cancel|migrate] --confirm",
            handled=False,
        )
    organization_id = _safe_key(positional[0])
    target_state = positional[1]
    if "confirm" not in options:
        return CommandResult(
            state.with_updates(status_message="organization lifecycle awaits explicit --confirm"),
            "org lifecycle is dry until --confirm is supplied",
        )
    grant = str(os.environ.get("ANANTA_ORGANIZATION_ADMIN_GRANT") or "").strip()
    if not grant:
        return CommandResult(
            state,
            "ANANTA_ORGANIZATION_ADMIN_GRANT is required; grants are never accepted as command arguments",
            handled=False,
        )
    summary = _unwrap(
        client.request_json(
            "GET",
            f"/api/organizations/{quote(organization_id, safe='')}",
        )
    )
    lock_version = summary.get("lock_version")
    if isinstance(lock_version, bool) or not isinstance(lock_version, int) or lock_version < 1:
        raise ValueError("organization_lock_version_missing")
    strategy = str(options.get("strategy") or "").strip() or None
    if strategy not in {None, "drain", "cancel", "migrate"}:
        raise ValueError("organization_active_work_strategy_invalid")
    body: dict[str, Any] = {
        "target_state": target_state,
        "admin_grant": grant,
        **({"active_work_strategy": strategy} if strategy else {}),
    }
    if strategy == "migrate":
        target = {
            "organization_id": str(options.get("target-organization") or "").strip(),
            "unit_id": str(options.get("target-unit") or "").strip(),
            "team_id": str(options.get("target-team") or "").strip(),
            "role_slot_id": str(options.get("target-role") or "").strip(),
        }
        if any(not value for value in target.values()):
            raise ValueError("organization_migration_target_required")
        body["migration_target"] = target
    result = _unwrap(
        client.request_json(
            "POST",
            f"/api/organizations/{quote(organization_id, safe='')}/lifecycle",
            payload=body,
            headers={
                "If-Match": f'"{lock_version}"',
                "Idempotency-Key": f"organization-tui-lifecycle:{secrets.token_urlsafe(18)}",
                "X-Organization-Admin-Grant": grant,
            },
            timeout=30.0,
        )
    )
    payload = _payload("lifecycle", result)
    return CommandResult(
        _show_payload(state, payload, f"organization lifecycle {target_state}"),
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _show_payload(state: OperatorState, payload: dict[str, Any], status: str) -> OperatorState:
    section_payloads = dict(state.section_payloads or {})
    section_payloads["artifacts"] = payload
    panel_states = dict(state.panel_states or {})
    panel_states["artifacts"] = PanelState.HEALTHY
    return state.with_updates(
        mode=OperatorMode.NORMAL,
        command_line="",
        section_id="artifacts",
        focus=FocusPane.CONTENT,
        selected_index=0,
        section_payloads=section_payloads,
        panel_states=panel_states,
        status_message=status,
    )


def _payload(view: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"organization_mode": True, "view": view, **data}


def _unwrap(value: dict[str, Any]) -> dict[str, Any]:
    current: Any = value
    for _ in range(4):
        if (
            isinstance(current, dict)
            and "data" in current
            and ("status" in current or set(current) <= {"data", "status", "message"})
        ):
            current = current.get("data")
        else:
            break
    if not isinstance(current, dict):
        raise ValueError("organization_response_invalid")
    return current


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _parse_options(args: list[str]) -> tuple[dict[str, str | bool], list[str]]:
    options: dict[str, str | bool] = {}
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = str(args[index])
        if token.startswith("--"):
            name = token[2:].lower()
            if name in {"confirm", "json", "mermaid"}:
                options[name] = True
            elif name in {
                "teams",
                "title",
                "depth",
                "page-size",
                "composition",
                "reason",
                "strategy",
                "target-organization",
                "target-unit",
                "target-team",
                "target-role",
            } and index + 1 < len(args):
                index += 1
                options[name] = str(args[index])
            else:
                raise ValueError(f"organization_option_invalid:{name}")
        else:
            positional.append(token)
        index += 1
    return options, positional


def _required_id(positional: list[str], state: OperatorState, usage: str) -> str | CommandResult:
    if not positional:
        return CommandResult(state, usage, handled=False)
    return _safe_key(positional[0])


def _safe_key(value: str) -> str:
    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > 128
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-" for char in candidate)
    ):
        raise ValueError("organization_identifier_invalid")
    return candidate


def _bounded_int(value: str | bool | None, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("organization_integer_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("organization_integer_invalid") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError("organization_integer_out_of_range")
    return parsed


def _parse_composition(value: str | bool) -> dict[str, int]:
    if isinstance(value, bool):
        raise ValueError("organization_custom_composition_invalid")
    result: dict[str, int] = {}
    for item in str(value or "").split(","):
        key, separator, raw_count = item.strip().partition("=")
        key = _safe_key(key)
        if not separator or key in result or not raw_count.isdigit():
            raise ValueError("organization_custom_composition_invalid")
        count = int(raw_count)
        if count < 1:
            raise ValueError("organization_custom_composition_invalid")
        result[key] = count
    if sum(result.values()) < 2:
        raise ValueError("organization_team_count_below_minimum")
    return result


def _redact_compile_plan(plan: dict[str, Any]) -> dict[str, Any]:
    projected = dict(plan)
    compile_token = str(projected.pop("compile_token", "") or "")
    admission_ref = str(projected.pop("admission_exception_ref", "") or "")
    projected["compile_token_issued"] = bool(compile_token)
    projected["admission_exception_issued"] = bool(admission_ref)
    return projected


def _token_from_state(state: OperatorState) -> str:
    audit = dict(state.audit_context or {})
    header = dict(state.header_logo_game or {})
    return str(
        audit.get("token")
        or audit.get("auth_token")
        or header.get("token")
        or header.get("auth_token")
        or os.environ.get("ANANTA_AUTH_TOKEN")
        or ""
    )


def _to_mermaid(payload: dict[str, Any]) -> str:
    topology = dict(payload.get("topology") or {})
    nodes = [dict(item) for item in list(topology.get("nodes") or []) if isinstance(item, dict)]
    edges = [dict(item) for item in list(topology.get("edges") or []) if isinstance(item, dict)]
    lines = ["flowchart TD"]
    for node in nodes[:500]:
        node_id = _mermaid_id(str(node.get("id") or "node"))
        label = str(node.get("label") or node.get("stable_key") or node_id).replace('"', "'")[:80]
        lines.append(f'  {node_id}["{label}"]')
    known = {_mermaid_id(str(node.get("id") or "node")) for node in nodes[:500]}
    for edge in edges[:2000]:
        source = _mermaid_id(str(edge.get("source_id") or ""))
        target = _mermaid_id(str(edge.get("target_id") or ""))
        if source in known and target in known:
            label = str(edge.get("kind") or "relation")[:40]
            lines.append(f"  {source} -->|{label}| {target}")
    if len(lines) == 1:
        lines.append('  empty["No topology loaded"]')
    return "\n".join(lines)


def _mermaid_id(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in value)
    return f"n_{normalized[:80]}"


def _usage(state: OperatorState) -> CommandResult:
    message = (
        "org status|blueprints|list|show <id> [--json|--mermaid]|planning <id>|proposals <id>|"
        "proposal <org-id> <proposal-id> approve|reject --confirm|"
        "validate <blueprint> [--teams N|--composition key=count,... --reason text]|"
        "instantiate [--confirm]|lifecycle <id> <state> [--strategy ...] --confirm|export <id> [--json]"
    )
    return CommandResult(state.with_updates(status_message=message), message, handled=False)
