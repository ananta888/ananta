#!/usr/bin/env python3
"""Validate workflow-runtime inventory, security policy and example documentation.

The check is deterministic and performs no network or filesystem writes.  It is
intentionally standard-library only so Compose/release images can run it without
documentation tooling.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

REQUIRED_THREAT_CLASSES = frozenset(
    {
        "prompt_injection",
        "tool_escalation",
        "confused_deputy",
        "replay_abuse",
        "state_poisoning",
        "history_checkpoint_tampering",
    }
)
REQUIRED_TRUST_COMPONENTS = frozenset({"hub", "worker", "temporal_server", "activities", "provider", "ui"})
RUNTIME_TARGETS = frozenset({"native", "langgraph", "temporal"})
PATH_CLASSIFICATIONS = frozenset({"live", "simulated", "degraded", "placeholder"})
BLOCKING_FINDING_STATUSES = frozenset({"open", "unmitigated", "unverified"})
SENSITIVE_KEY_MARKERS = ("password", "private_key", "secret", "token", "api_key")


def _load_json(root: Path, relative_path: str) -> Any:
    path = root / relative_path
    return json.loads(path.read_text(encoding="utf-8"))


def _reference_exists(root: Path, reference: str) -> bool:
    path_text, separator, node = str(reference).partition("::")
    path = root / path_text
    if not path.exists():
        return False
    if not separator:
        return True
    text = path.read_text(encoding="utf-8")
    node_name = node.split("[", 1)[0].split("::")[-1]
    return bool(
        re.search(rf"^(?:async\s+)?def\s+{re.escape(node_name)}\s*\(", text, re.MULTILINE)
        or re.search(rf"^class\s+{re.escape(node_name)}(?:\s*\(|\s*:)", text, re.MULTILINE)
    )


def _validate_security_gate(root: Path, gate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if gate.get("schema") != "ananta.workflow_runtime_security_gates.v1":
        errors.append("security_gate_schema_invalid")
    policy = gate.get("production_policy") or {}
    if not policy.get("fail_closed") or not policy.get("require_all_mapped_tests"):
        errors.append("security_gate_production_policy_not_fail_closed")
    if "critical" not in set(policy.get("blocking_severities") or ()):
        errors.append("security_gate_critical_not_blocking")

    boundaries = gate.get("trust_boundaries") or []
    boundary_ids = {str(item.get("id") or "") for item in boundaries if isinstance(item, dict)}
    components = {
        str(component) for item in boundaries if isinstance(item, dict) for component in (item.get("components") or ())
    }
    missing_components = REQUIRED_TRUST_COMPONENTS - components
    if missing_components:
        errors.append("security_gate_missing_trust_components:" + ",".join(sorted(missing_components)))

    threats = gate.get("threats") or []
    threat_classes = {str(item.get("class") or "") for item in threats if isinstance(item, dict)}
    missing_threats = REQUIRED_THREAT_CLASSES - threat_classes
    if missing_threats:
        errors.append("security_gate_missing_threat_classes:" + ",".join(sorted(missing_threats)))
    for threat in threats:
        if not isinstance(threat, dict):
            errors.append("security_gate_threat_not_object")
            continue
        threat_id = str(threat.get("id") or "missing-id")
        if not threat_id or not threat.get("title") or not threat.get("severity"):
            errors.append(f"{threat_id}:security_gate_threat_identity_incomplete")
        unknown_boundaries = set(threat.get("trust_boundaries") or ()) - boundary_ids
        if unknown_boundaries:
            errors.append(f"{threat_id}:unknown_trust_boundaries:{','.join(sorted(unknown_boundaries))}")
        for field_name in ("prevention", "detection", "audit", "tests"):
            value = threat.get(field_name)
            if not isinstance(value, list) or not value or any(not str(item).strip() for item in value):
                errors.append(f"{threat_id}:{field_name}_mapping_missing")
        if threat.get("severity") == "critical" and threat.get("status") in BLOCKING_FINDING_STATUSES:
            errors.append(f"{threat_id}:open_critical_finding_blocks_production")
        for test_ref in threat.get("tests") or ():
            if not _reference_exists(root, str(test_ref)):
                errors.append(f"{threat_id}:mapped_test_missing:{test_ref}")

    for finding in gate.get("open_findings") or ():
        if not isinstance(finding, dict):
            errors.append("security_gate_open_finding_not_object")
            continue
        if finding.get("severity") == "critical":
            errors.append(f"{finding.get('id', 'unknown')}:open_critical_finding_blocks_production")
    return errors


def _assigned_string_collection(path: Path, assignment_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or target.id != assignment_name or value is None:
            continue
        if isinstance(value, ast.Call) and len(value.args) == 1:
            value = value.args[0]
        literal = ast.literal_eval(value)
        if isinstance(literal, dict):
            return {str(item) for item in literal}
        return {str(item) for item in literal}
    raise ValueError(f"assignment_not_found:{assignment_name}")


def _registered_worker_adapter_kinds(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "register_adapter" or not node.args:
            continue
        adapter_call = node.args[0]
        if not isinstance(adapter_call, ast.Call) or not isinstance(adapter_call.func, ast.Name):
            continue
        class_name = adapter_call.func.id
        if class_name.endswith("Adapter"):
            kinds.add(class_name.removesuffix("Adapter").lower())
    return kinds


def _active_task_ids(root: Path) -> set[str]:
    todo = _load_json(root, "todos/todo.production-ai-workflow-runtime-ananta-native-langchain-langgraph-temporal.json")
    return {str(item.get("id") or "") for item in todo.get("tasks") or ()}


def _validate_inventory(root: Path, inventory: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if inventory.get("schema") != "ananta.workflow_runtime_inventory.v1":
        errors.append("runtime_inventory_schema_invalid")
    selectors = inventory.get("documented_selectors") or {}
    documented_backends = set(selectors.get("workflow_backends") or ())
    documented_adapters = set(selectors.get("worker_adapters") or ())
    documented_targets = set(selectors.get("production_runtime_targets") or ())

    code_backends = _assigned_string_collection(
        root / "agent/services/workflow_backend_factory.py", "SUPPORTED_WORKFLOW_BACKENDS"
    )
    catalog_adapters = _assigned_string_collection(
        root / "agent/services/workflow_adapter_catalog_service.py", "_SAFE_ADAPTER_DESCRIPTORS"
    )
    registered_adapters = _registered_worker_adapter_kinds(root / "worker/adapters/workflow_adapter_registry.py")
    if code_backends != documented_backends:
        errors.append(
            "runtime_inventory_backend_selector_drift:"
            f"code={','.join(sorted(code_backends))}:docs={','.join(sorted(documented_backends))}"
        )
    discovered_adapters = catalog_adapters | registered_adapters
    if discovered_adapters != documented_adapters:
        errors.append(
            "runtime_inventory_adapter_drift:"
            f"code={','.join(sorted(discovered_adapters))}:docs={','.join(sorted(documented_adapters))}"
        )
    if documented_targets != RUNTIME_TARGETS:
        errors.append("runtime_inventory_production_targets_incomplete")

    task_ids = _active_task_ids(root)
    for path_entry in inventory.get("paths") or ():
        if not isinstance(path_entry, dict):
            errors.append("runtime_inventory_path_not_object")
            continue
        entry_id = str(path_entry.get("id") or "missing-id")
        for field_name in ("owner", "entrypoint", "call_path", "gap", "followup_task"):
            if not str(path_entry.get(field_name) or "").strip():
                errors.append(f"{entry_id}:runtime_inventory_{field_name}_missing")
        if path_entry.get("classification") not in PATH_CLASSIFICATIONS:
            errors.append(f"{entry_id}:runtime_inventory_classification_invalid")
        if not path_entry.get("containers"):
            errors.append(f"{entry_id}:runtime_inventory_containers_missing")
        if path_entry.get("followup_task") not in task_ids:
            errors.append(f"{entry_id}:runtime_inventory_followup_unknown")
        evidence = path_entry.get("evidence") or ()
        if not evidence:
            errors.append(f"{entry_id}:runtime_inventory_evidence_missing")
        for reference in evidence:
            if not _reference_exists(root, str(reference)):
                errors.append(f"{entry_id}:runtime_inventory_evidence_not_found:{reference}")

    capability_runtimes: set[str] = set()
    for capability in inventory.get("capabilities") or ():
        if not isinstance(capability, dict):
            errors.append("runtime_inventory_capability_not_object")
            continue
        runtime = str(capability.get("runtime") or "")
        name = str(capability.get("capability") or "")
        capability_id = f"{runtime}:{name}"
        capability_runtimes.add(runtime)
        for field_name in ("mode", "evidence", "gap", "followup_task"):
            if not str(capability.get(field_name) or "").strip():
                errors.append(f"{capability_id}:capability_{field_name}_missing")
        if capability.get("followup_task") not in task_ids:
            errors.append(f"{capability_id}:capability_followup_unknown")
        if not _reference_exists(root, str(capability.get("evidence") or "")):
            errors.append(f"{capability_id}:capability_evidence_not_found")
    if not RUNTIME_TARGETS.issubset(capability_runtimes):
        errors.append("runtime_inventory_runtime_capability_mapping_incomplete")

    for claim in inventory.get("archived_claims") or ():
        if not isinstance(claim, dict):
            errors.append("runtime_inventory_archive_claim_not_object")
            continue
        archive = str(claim.get("archive") or "")
        if not archive or not (root / archive).is_file():
            errors.append(f"runtime_inventory_archive_missing:{archive}")
        for field_name in ("claim", "historical_contradiction", "current_resolution"):
            if not str(claim.get(field_name) or "").strip():
                errors.append(f"{archive}:archive_{field_name}_missing")
        followups = set(claim.get("followup_tasks") or ())
        if not followups or not followups.issubset(task_ids):
            errors.append(f"{archive}:archive_followup_unknown")
    return errors


def _sensitive_fixture_paths(value: Any, path: str = "$") -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower().replace("-", "_").replace(" ", "_")
            if any(
                key_text == marker or key_text.startswith(f"{marker}_") or key_text.endswith(f"_{marker}")
                for marker in SENSITIVE_KEY_MARKERS
            ):
                yield f"{path}.{key}"
            yield from _sensitive_fixture_paths(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _sensitive_fixture_paths(child, f"{path}[{index}]")


def _validate_live_provider_example(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    live_profile_ref = str(manifest.get("live_provider_profile_ref") or "")
    live_profile = _load_json(root, live_profile_ref)
    if live_profile.get("schema") != "ananta.workflow_runtime_example_live_provider_profile.v1":
        errors.append("workflow_runtime_example_live_provider_schema_invalid")
    live_langchain = live_profile.get("langchain") or {}
    if (
        live_langchain.get("role") != "provider_adapter"
        or live_langchain.get("control_plane") is not False
        or live_langchain.get("task_queue_owner") is not False
        or live_langchain.get("workflow_scheduler") is not False
    ):
        errors.append("workflow_runtime_example_live_provider_boundary_invalid")
    if set(live_langchain.get("allowed_roles") or ()) != {
        "provider_adapter",
        "retriever_adapter",
        "tool_adapter",
    }:
        errors.append("workflow_runtime_example_live_provider_roles_invalid")
    live_config = live_profile.get("configuration") or {}
    required_env_refs = {
        "base_url_env": "ANANTA_EXAMPLE_LIVE_PROVIDER_BASE_URL",
        "model_env": "ANANTA_EXAMPLE_LIVE_PROVIDER_MODEL",
        "credential_file_env": "ANANTA_EXAMPLE_LIVE_PROVIDER_API_KEY_FILE",
    }
    if any(live_config.get(key) != value for key, value in required_env_refs.items()):
        errors.append("workflow_runtime_example_live_provider_env_refs_invalid")
    if not set(live_config.get("allowed_hostnames") or ()).issubset(
        {"127.0.0.1", "host.docker.internal", "localhost", "ollama"}
    ):
        errors.append("workflow_runtime_example_live_provider_not_local")
    live_limits = live_profile.get("limits") or {}
    if (
        not 0 < int(live_limits.get("timeout_seconds") or 0) <= 60
        or not 0 < int(live_limits.get("max_tokens") or 0) <= 128
        or not 0 < int(live_limits.get("maximum_response_bytes") or 0) <= 1_048_576
    ):
        errors.append("workflow_runtime_example_live_provider_limits_invalid")
    if (live_profile.get("evidence") or {}).get("production_release_gate") is not False:
        errors.append("workflow_runtime_example_live_provider_release_gate_forbidden")

    live_overlay_ref = str((manifest.get("compose") or {}).get("optional_live_provider_overlay") or "")
    live_overlay = (root / live_overlay_ref).read_text(encoding="utf-8")
    for marker in (
        "ANANTA_EXAMPLE_LIVE_PROVIDER_BASE_URL:?",
        "ANANTA_EXAMPLE_LIVE_PROVIDER_MODEL:?",
        "ANANTA_EXAMPLE_LIVE_PROVIDER_API_KEY_FILE:?",
        "workflow-runtime-live-provider-api-key",
        "no-new-privileges:true",
        'cap_drop: ["ALL"]',
    ):
        if marker not in live_overlay:
            errors.append(f"workflow_runtime_example_live_overlay_marker_missing:{marker}")
    if "api_key:" in live_overlay.lower() or "docker.sock" in live_overlay:
        errors.append("workflow_runtime_example_live_overlay_secret_or_socket_exposed")
    return errors


def _validate_example_ci(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ci_ref = str((manifest.get("runner") or {}).get("ci_workflow") or "")
    ci_text = (root / ci_ref).read_text(encoding="utf-8")
    for marker in (
        "--mode prepare",
        "kill -s SIGKILL workflow-runtime-example-temporal-worker",
        "--mode resume",
        "--mode validate-evidence",
        "if: always()",
        "down --volumes --remove-orphans",
        "timeout-minutes: 30",
    ):
        if marker not in ci_text:
            errors.append(f"workflow_runtime_example_ci_marker_missing:{marker}")
    return errors


def _validate_example(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != "ananta.workflow_runtime_example.v1":
        errors.append("workflow_runtime_example_schema_invalid")
    plan_ref = str(manifest.get("execution_plan_ref") or "")
    if not _reference_exists(root, plan_ref):
        errors.append("workflow_runtime_example_plan_missing")
        return errors
    runtimes = manifest.get("runtimes") or {}
    if set(runtimes) != RUNTIME_TARGETS:
        errors.append("workflow_runtime_example_runtimes_incomplete")
    for runtime_name, runtime in runtimes.items():
        if not isinstance(runtime, dict) or runtime.get("execution_plan_ref") != plan_ref:
            errors.append(f"workflow_runtime_example_plan_drift:{runtime_name}")
    if not bool((runtimes.get("temporal") or {}).get("durable")):
        errors.append("workflow_runtime_example_temporal_not_durable")
    langchain = manifest.get("langchain") or {}
    if langchain.get("control_plane") is not False or langchain.get("task_queue_owner") is not False:
        errors.append("workflow_runtime_example_langchain_boundary_invalid")
    if set(langchain.get("allowed_roles") or ()) != {
        "provider_adapter",
        "retriever_adapter",
        "tool_adapter",
    }:
        errors.append("workflow_runtime_example_langchain_roles_invalid")
    scenarios = manifest.get("scenarios") or {}
    if set(scenarios) != {"failure", "approval", "cancel", "crash", "resume"}:
        errors.append("workflow_runtime_example_scenarios_incomplete")
    for name, scenario in scenarios.items():
        if not isinstance(scenario, dict) or not scenario.get("trigger") or not scenario.get("expected_events"):
            errors.append(f"workflow_runtime_example_scenario_incomplete:{name}")

    referenced = [
        plan_ref,
        str(manifest.get("fake_provider_ref") or ""),
        str(manifest.get("live_provider_profile_ref") or ""),
        str(manifest.get("rollout_policy_ref") or ""),
        str((manifest.get("runner") or {}).get("ci_workflow") or ""),
        *[str(value) for value in (manifest.get("compose") or {}).values()],
    ]
    for reference in referenced:
        if not _reference_exists(root, reference):
            errors.append(f"workflow_runtime_example_reference_missing:{reference}")

    plan = _load_json(root, plan_ref)
    try:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        from agent.services.workflow_runtime.execution_plan import ExecutionPlan

        parsed_plan = ExecutionPlan.from_mapping(plan)
        parsed_plan.assert_valid()
        if parsed_plan.to_dict() != plan:
            errors.append("workflow_runtime_example_plan_roundtrip_drift")
    except Exception as exc:  # noqa: BLE001 - validation should report one stable failure
        errors.append(f"workflow_runtime_example_plan_invalid:{type(exc).__name__}")

    fake = _load_json(root, str(manifest.get("fake_provider_ref") or ""))
    if fake.get("network_access") is not False:
        errors.append("workflow_runtime_example_fake_provider_network_enabled")
    for node_id, attempts in (fake.get("responses") or {}).items():
        attempt_numbers = [item.get("attempt") for item in attempts if isinstance(item, dict)]
        if attempt_numbers != list(range(1, len(attempt_numbers) + 1)):
            errors.append(f"workflow_runtime_example_fake_attempts_unstable:{node_id}")

    errors.extend(_validate_live_provider_example(root, manifest))
    errors.extend(_validate_example_ci(root, manifest))

    rollout = _load_json(root, str(manifest.get("rollout_policy_ref") or ""))
    if rollout.get("merge_rule") != "narrow_only" or rollout.get("scope_precedence") != [
        "project",
        "tenant",
        "profile",
        "workflow",
    ]:
        errors.append("workflow_runtime_example_rollout_precedence_invalid")
    parent_allowed: set[str] | None = None
    parent_required: set[str] = set()
    for scope_name in rollout.get("scope_precedence") or ():
        scope = rollout.get(scope_name) or {}
        allowed = set(scope.get("allowed_runtimes") or ())
        required = set(scope.get("required_capabilities") or ())
        if parent_allowed is not None and not allowed.issubset(parent_allowed):
            errors.append(f"workflow_runtime_example_rollout_expands_allowlist:{scope_name}")
        if not parent_required.issubset(required):
            errors.append(f"workflow_runtime_example_rollout_drops_requirement:{scope_name}")
        parent_allowed, parent_required = allowed, required
    workflow_scope = rollout.get("workflow") or {}
    shadow = workflow_scope.get("shadow") or {}
    if workflow_scope.get("mode") != "shadow" or shadow.get("network_access") is not False:
        errors.append("workflow_runtime_example_shadow_not_fail_closed")
    if shadow.get("write_behavior") != "suppress_and_record_intent":
        errors.append("workflow_runtime_example_shadow_write_not_suppressed")

    for reference in (
        plan_ref,
        manifest.get("fake_provider_ref"),
        manifest.get("live_provider_profile_ref"),
        manifest.get("rollout_policy_ref"),
    ):
        fixture = _load_json(root, str(reference))
        sensitive = list(_sensitive_fixture_paths(fixture))
        if sensitive:
            errors.append(f"workflow_runtime_example_sensitive_fixture_keys:{reference}:{','.join(sensitive)}")
    return errors


def _validate_document_contracts(root: Path) -> list[str]:
    errors: list[str] = []
    requirements: dict[str, tuple[str, ...]] = {
        "docs/decisions/ADR-workflow-runtime-control-state-boundaries.md": (
            "ExecutionRuntimePort",
            "DurableRunInfrastructurePort",
            "State and checkpoint authority",
            "Migration and compatibility",
            "Exit strategy",
            "Rejected alternatives",
            "Temporal Activities are executors",
        ),
        "docs/operations/workflow-runtime-rollout.md": (
            "project, tenant, profile and workflow",
            "suppress_and_record_intent",
            "Capability-safe rollback",
            "Backup and restore",
            "Schema migration",
            "Key rotation",
            "Incident response",
            "ANANTA_WORKFLOW_OTEL_ENABLED",
            "ANANTA_WORKFLOW_OTEL_ENDPOINT",
            "ANANTA_WORKFLOW_OTEL_HEADERS_FILE",
        ),
        "docs/architecture/workflow-runtime.md": (
            "The Hub remains the sole control plane",
            "Canonical events",
            "same",
            "Native",
            "LangGraph",
            "Temporal",
            "LangChain",
            "ANANTA_WORKFLOW_OTEL_ENABLED",
            "16 KiB",
        ),
        "docs/security/workflow-runtime-threat-model.md": (
            "Prompt injection",
            "Tool escalation",
            "Confused deputy",
            "Replay",
            "State poisoning",
            "History/checkpoint tampering",
            "production",
        ),
        "docs/examples/workflow-runtime/README.md": (
            "Failure",
            "Approval",
            "Cancel",
            "Crash",
            "Resume",
            "docker/compose-next",
            "Optional local live-provider profile",
            "workflow-runtime-example.yml",
        ),
    }
    for relative_path, markers in requirements.items():
        path = root / relative_path
        if not path.is_file():
            errors.append(f"workflow_runtime_document_missing:{relative_path}")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker.lower() not in text.lower():
                errors.append(f"workflow_runtime_document_marker_missing:{relative_path}:{marker}")
    return errors


def validate_repository(root: Path) -> list[str]:
    errors: list[str] = []
    security_gate = _load_json(root, "docs/security/workflow-runtime-security-gates.v1.json")
    inventory = _load_json(root, "docs/architecture/workflow-runtime-inventory.v1.json")
    example = _load_json(root, "examples/workflow-runtime/example-manifest.v1.json")
    errors.extend(_validate_security_gate(root, security_gate))
    errors.extend(_validate_inventory(root, inventory))
    errors.extend(_validate_example(root, example))
    errors.extend(_validate_document_contracts(root))
    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        errors = validate_repository(root)
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
        print(f"workflow-runtime-docs: validation_error:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"workflow-runtime-docs: {error}", file=sys.stderr)
        return 1
    print("workflow-runtime-docs: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
