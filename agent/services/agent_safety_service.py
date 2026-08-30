"""Hub-owned coordination for prevention-independent agent safety controls."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from agent.services.agent_safety_admission_policy import AgentSafetyAdmissionPolicy
from agent.services.agent_safety_errors import AgentSafetyDenied
from agent.services.agent_safety_ports import (
    CredentialLeaseAuthorityPort,
    CredentialLeaseRevocationPort,
    EgressFencePort,
    ForensicSnapshotPort,
    SandboxSafetyControlPort,
)
from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from ananta_contracts.agent_safety import (
    BoundaryClass,
    RuntimeEventType,
    SafetyAction,
    SafetyEvent,
    SafetyMode,
    SafetyPolicy,
    SentinelManifest,
    StopScope,
    TriggerClass,
    canonical_digest,
    require_token,
    utc_now,
)


class AgentSafetyControlService:
    """Coordinates small safety ports under Hub-owned durable state.

    An unavailable containment adapter is a denial, never a successful no-op.
    """

    def __init__(
        self,
        store: AgentSafetyStateStorePort,
        *,
        manifest_signing_key: bytes,
        sandbox_control: SandboxSafetyControlPort,
        egress_fence: EgressFencePort,
        credential_revocation: CredentialLeaseRevocationPort,
        admission_policy: AgentSafetyAdmissionPolicy | None = None,
        credential_lease_authority: CredentialLeaseAuthorityPort | None = None,
        forensic_snapshot: ForensicSnapshotPort | None = None,
    ) -> None:
        if len(manifest_signing_key) < 32:
            raise ValueError("agent_safety_manifest_signing_key_too_short")
        self._store = store
        self._manifest_signing_key = bytes(manifest_signing_key)
        self._sandbox_control = sandbox_control
        self._egress_fence = egress_fence
        self._credential_revocation = credential_revocation
        self._admission_policy = admission_policy
        self._credential_lease_authority = credential_lease_authority
        self._forensic_snapshot = forensic_snapshot

    def configure_policy(self, **payload: Any) -> dict[str, Any]:
        mode = SafetyMode(str(payload.get("mode") or SafetyMode.ENFORCE))
        policy = SafetyPolicy(
            policy_id=require_token(payload.get("policy_id"), "policy_id"),
            revision=int(payload.get("revision") or 1),
            mode=mode,
            preventive_policy_enabled=bool(payload.get("preventive_policy_enabled", True)),
            preventive_training_enabled=bool(payload.get("preventive_training_enabled", False)),
            telemetry_enabled=bool(payload.get("telemetry_enabled", True)),
            external_kill_switch_enabled=bool(payload.get("external_kill_switch_enabled", True)),
            incident_freeze_enabled=bool(payload.get("incident_freeze_enabled", True)),
            sentinel_enabled=bool(payload.get("sentinel_enabled", True)),
            adversarial_evaluation_enabled=bool(
                payload.get("adversarial_evaluation_enabled", mode == SafetyMode.ADVERSARIAL_EVAL)
            ),
            adversarial_scope=tuple(str(item) for item in payload.get("adversarial_scope") or ()),
            global_stop_scope=StopScope(str(payload.get("global_stop_scope") or StopScope.RUN)),
            max_parallel_agents=int(payload.get("max_parallel_agents") or 1),
            max_trace_events=int(payload.get("max_trace_events") or 10_000),
            freeze_ttl_seconds=int(payload.get("freeze_ttl_seconds") or 900),
            max_snapshot_bytes=int(payload.get("max_snapshot_bytes") or 262_144),
        )
        current = self._store.get("policy", policy.policy_id)
        expected = int(current.get("revision") or 0) if current else 0
        if current and policy.revision <= int(current.get("policy_revision") or 0):
            raise AgentSafetyDenied("agent_safety_policy_revision_not_monotonic")
        authorization = (
            self._admission_policy.authorize_configuration(payload) if self._admission_policy is not None else None
        )
        return self._store.append(
            "policy",
            policy.policy_id,
            {
                **policy.as_dict(),
                "policy_revision": policy.revision,
                "configured_at": utc_now(),
                "authorization": authorization,
            },
            expected_revision=expected,
        )

    def register_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        group_id: str,
        policy_id: str,
        target_ref: str,
        agents: list[Mapping[str, Any]],
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = self._require_policy(policy_id)
        if len(agents) < 1 or len(agents) > int(policy["max_parallel_agents"]):
            raise AgentSafetyDenied("agent_safety_run_parallelism_denied")
        mode = SafetyMode(policy["mode"])
        if mode == SafetyMode.ADVERSARIAL_EVAL and target_ref not in set(policy["adversarial_scope"]):
            raise AgentSafetyDenied("agent_safety_target_not_authorized")
        normalized_agents = []
        seen: set[str] = set()
        for value in agents:
            agent_id = require_token(value.get("agent_id"), "agent_id")
            sandbox_id = require_token(value.get("sandbox_id"), "sandbox_id")
            if agent_id in seen:
                raise ValueError("agent_safety_duplicate_agent")
            seen.add(agent_id)
            normalized_agents.append({"agent_id": agent_id, "sandbox_id": sandbox_id, "state": "active"})
        run = {
            "tenant_id": require_token(tenant_id, "tenant_id"),
            "project_id": require_token(project_id, "project_id"),
            "run_id": require_token(run_id, "run_id"),
            "group_id": require_token(group_id, "group_id"),
            "policy_id": require_token(policy_id, "policy_id"),
            "policy_revision": int(policy["policy_revision"]),
            "target_ref": require_token(target_ref, "target_ref"),
            "mode": mode.value,
            "state": "active",
            "execution_allowed": True,
            "agents": normalized_agents,
            "provenance": _normalized_provenance(provenance),
            "registered_at": utc_now(),
        }
        stored = self._store.append("run", run_id, run, expected_revision=0)
        if self._credential_lease_authority is None:
            return stored
        grants = [
            self._credential_lease_authority.issue(
                run_id=run_id,
                agent_id=str(agent["agent_id"]),
                ttl_seconds=min(int(policy["freeze_ttl_seconds"]), 3_600),
            )
            for agent in normalized_agents
        ]
        stored = self._store.append(
            "run",
            run_id,
            {**stored, "credential_lease_refs": [grant.lease_id for grant in grants]},
            expected_revision=int(stored["revision"]),
        )
        return {**stored, "credential_lease_grants": [asdict(grant) for grant in grants]}

    def register_run_group(
        self,
        *,
        tenant_id: str,
        project_id: str,
        group_id: str,
        run_prefix: str,
        policy_id: str,
        target_ref: str,
        agents: list[Mapping[str, Any]],
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = self._require_policy(policy_id)
        if not 1 <= len(agents) <= int(policy["max_parallel_agents"]):
            raise AgentSafetyDenied("agent_safety_run_parallelism_denied")
        normalized_prefix = require_token(run_prefix, "run_prefix")
        identities = [
            (
                require_token(value.get("agent_id"), "agent_id"),
                require_token(value.get("sandbox_id"), "sandbox_id"),
            )
            for value in agents
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("agent_safety_duplicate_agent_binding")
        run_ids = [f"{normalized_prefix}-{index + 1}" for index in range(len(identities))]
        if any(self._store.get("run", run_id) for run_id in run_ids):
            raise AgentSafetyDenied("agent_safety_run_group_id_conflict")
        runs = [
            self.register_run(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                group_id=group_id,
                policy_id=policy_id,
                target_ref=target_ref,
                agents=[{"agent_id": agent_id, "sandbox_id": sandbox_id}],
                provenance=provenance,
            )
            for run_id, (agent_id, sandbox_id) in zip(run_ids, identities, strict=True)
        ]
        return {
            "group_id": require_token(group_id, "group_id"),
            "run_ids": run_ids,
            "runs": runs,
            "parallel_agent_count": len(runs),
            "human_intervention_required": False,
        }

    def issue_sentinel(
        self,
        *,
        run_id: str,
        sandbox_id: str,
        trigger_class: str,
        effect: str,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        run = self._require_active_run(run_id)
        policy = self._require_policy(str(run["policy_id"]))
        if policy.get("sentinel_enabled", True) is False:
            raise AgentSafetyDenied("agent_safety_sentinel_disabled")
        if sandbox_id not in {item["sandbox_id"] for item in run["agents"]}:
            raise AgentSafetyDenied("agent_safety_sandbox_not_bound")
        trigger = TriggerClass(trigger_class)
        if trigger == TriggerClass.OPAQUE_PRIORITY:
            trigger_id = secrets.token_hex(16)
            visibility = "opaque"
        else:
            trigger_id = trigger.value
            visibility = "open"
        now = datetime.now(timezone.utc)
        manifest = SentinelManifest(
            manifest_id=f"asm_{uuid.uuid4().hex}",
            tenant_id=run["tenant_id"],
            project_id=run["project_id"],
            run_id=run["run_id"],
            sandbox_id=require_token(sandbox_id, "sandbox_id"),
            trigger_id=trigger_id,
            trigger_class=trigger,
            policy_id=run["policy_id"],
            policy_revision=int(run["policy_revision"]),
            policy_mode=SafetyMode(run["mode"]),
            manifest_version=1,
            nonce=secrets.token_hex(16),
            issued_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=min(max(int(ttl_seconds), 30), 3600))),
            effect=SafetyAction(effect),
            priority=100,
            visibility=visibility,
        ).sign(self._manifest_signing_key)
        value = manifest.as_dict()
        value["injection_channel"] = "hub_runtime_control"
        return self._store.append("sentinel_manifest", manifest.manifest_id, value, expected_revision=0)

    def consume_sentinel(self, *, manifest: Mapping[str, Any], agent_id: str) -> dict[str, Any]:
        stored = self._store.get("sentinel_manifest", str(manifest.get("manifest_id") or ""))
        if not stored:
            raise AgentSafetyDenied("agent_safety_manifest_not_hub_issued")
        parsed = SentinelManifest(
            manifest_id=str(manifest.get("manifest_id") or ""),
            tenant_id=str(manifest.get("tenant_id") or ""),
            project_id=str(manifest.get("project_id") or ""),
            run_id=str(manifest.get("run_id") or ""),
            sandbox_id=str(manifest.get("sandbox_id") or ""),
            trigger_id=str(manifest.get("trigger_id") or ""),
            trigger_class=TriggerClass(str(manifest.get("trigger_class"))),
            policy_id=str(manifest.get("policy_id") or ""),
            policy_revision=int(manifest.get("policy_revision") or 0),
            policy_mode=SafetyMode(str(manifest.get("policy_mode"))),
            manifest_version=int(manifest.get("manifest_version") or 0),
            nonce=str(manifest.get("nonce") or ""),
            issued_at=str(manifest.get("issued_at") or ""),
            expires_at=str(manifest.get("expires_at") or ""),
            effect=SafetyAction(str(manifest.get("effect"))),
            priority=int(manifest.get("priority") or 0),
            visibility=str(manifest.get("visibility") or ""),
            signature=str(manifest.get("signature") or ""),
        )
        parsed.verify(
            self._manifest_signing_key,
            now=utc_now(),
            expected_run_id=str(stored.get("run_id") or ""),
            expected_sandbox_id=str(stored.get("sandbox_id") or ""),
        )
        if any(stored.get(key) != value for key, value in parsed.as_dict().items()):
            raise AgentSafetyDenied("agent_safety_manifest_not_hub_issued")
        self._require_agent_binding(parsed.run_id, parsed.sandbox_id, agent_id)
        self._store.append(
            "trigger_consumption",
            parsed.manifest_id,
            {"run_id": parsed.run_id, "manifest_id": parsed.manifest_id, "consumed_at": utc_now()},
            expected_revision=0,
        )
        self._require_active_run(parsed.run_id)
        event = self._record_event(
            run_id=parsed.run_id,
            sandbox_id=parsed.sandbox_id,
            agent_id=agent_id,
            event_type="sentinel_triggered",
            severity="critical" if parsed.effect != SafetyAction.FREEZE else "high",
            source="agent_self_report",
            details={"manifest_id": parsed.manifest_id, "trigger_class": parsed.trigger_class.value},
        )
        containment = self.emergency_stop(
            run_id=parsed.run_id,
            action=parsed.effect.value,
            reason=f"sentinel:{event['event_id']}",
        )
        return {"event": event, "containment": containment}

    def record_boundary_event(
        self,
        *,
        run_id: str,
        sandbox_id: str,
        agent_id: str,
        boundary_class: str,
        outcome: str,
        detector_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        BoundaryClass(boundary_class)
        if outcome not in {"allowed", "blocked", "crossed"}:
            raise ValueError("agent_safety_boundary_outcome_invalid")
        self._require_agent_binding(run_id, sandbox_id, agent_id)
        event = self._record_event(
            run_id=run_id,
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            event_type="boundary_crossing",
            severity="critical" if outcome == "crossed" else "warning" if outcome == "blocked" else "info",
            source=require_token(detector_id, "detector_id"),
            details={"boundary_class": boundary_class, "outcome": outcome, **dict(metadata or {})},
        )
        if outcome == "crossed":
            event["containment"] = self.emergency_stop(
                run_id=run_id, action="freeze", reason=f"boundary:{event['event_id']}"
            )
        return event

    def record_runtime_event(
        self,
        *,
        run_id: str,
        sandbox_id: str,
        agent_id: str,
        event_type: str,
        detector_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime_type = RuntimeEventType(event_type)
        self._require_agent_binding(run_id, sandbox_id, agent_id)
        run = self._require_active_run(run_id)
        details = _telemetry_details(
            metadata or {}, high_fidelity=SafetyMode(str(run["mode"])) == SafetyMode.ADVERSARIAL_EVAL
        )
        return self._record_event(
            run_id=run_id,
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            event_type=runtime_type.value,
            severity="info",
            source=require_token(detector_id, "detector_id"),
            details=details,
        )

    def emergency_stop(self, *, run_id: str, action: str, reason: str) -> dict[str, Any]:
        source_run = self._require_run(run_id)
        policy = self._require_policy(source_run["policy_id"])
        selected = self._runs_for_scope(source_run, StopScope(policy["global_stop_scope"]))
        operation_id = f"aso_{uuid.uuid4().hex}"
        receipts: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        all_enforced = True
        for run in selected:
            for agent in run["agents"]:
                control = self._sandbox_control.apply(
                    operation_id=operation_id,
                    run_id=run["run_id"],
                    sandbox_id=agent["sandbox_id"],
                    action=SafetyAction(action),
                    reason=reason,
                )
                fence = self._egress_fence.deny(
                    operation_id=operation_id,
                    run_id=run["run_id"],
                    sandbox_id=agent["sandbox_id"],
                )
                receipts.extend([control.as_dict(), fence.as_dict()])
                all_enforced = all_enforced and control.enforced and fence.enforced
                if (
                    SafetyAction(action) == SafetyAction.FREEZE
                    and bool(policy.get("incident_freeze_enabled"))
                    and self._forensic_snapshot is not None
                ):
                    snapshots.append(
                        self._forensic_snapshot.capture(
                            operation_id=operation_id,
                            run_id=str(run["run_id"]),
                            sandbox_id=str(agent["sandbox_id"]),
                            max_bytes=int(policy.get("max_snapshot_bytes") or 262_144),
                        )
                    )
            revoked = self._credential_revocation.revoke(operation_id=operation_id, run_id=run["run_id"])
            receipts.append(revoked.as_dict())
            all_enforced = all_enforced and revoked.enforced
            current = self._require_run(run["run_id"])
            state = SafetyAction(action).value if all_enforced else "containment_failed_closed"
            freeze_expires_at = (
                _iso(datetime.now(timezone.utc) + timedelta(seconds=int(policy["freeze_ttl_seconds"])))
                if SafetyAction(action) == SafetyAction.FREEZE
                else None
            )
            self._store.append(
                "run",
                run["run_id"],
                {
                    **current,
                    "state": state,
                    "execution_allowed": False,
                    "stop_reason": reason,
                    "stopped_at": utc_now(),
                    "freeze_expires_at": freeze_expires_at,
                },
                expected_revision=int(current["revision"]),
            )
        result = {
            "operation_id": operation_id,
            "run_id": source_run["run_id"],
            "scope": policy["global_stop_scope"],
            "action": SafetyAction(action).value,
            "state": "enforced" if all_enforced else "failed_closed",
            "receipts": receipts,
            "snapshot_receipts": snapshots,
            "runtime_evidence_verified": bool(receipts)
            and all(bool(receipt.get("runtime_verified")) for receipt in receipts),
            "completed_at": utc_now(),
        }
        stored = self._store.append("control_operation", operation_id, result, expected_revision=0)
        stored["incident"] = self.create_incident_bundle(run_id=source_run["run_id"], operation_id=operation_id)
        return stored

    def create_incident_bundle(self, *, run_id: str, operation_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        events = self._store.list_events(run_id=run_id, limit=10_000)
        operation = self._store.get("control_operation", operation_id)
        bundle_id = f"asib_{uuid.uuid4().hex}"
        payload = {
            "bundle_id": bundle_id,
            "tenant_id": run["tenant_id"],
            "project_id": run["project_id"],
            "run_id": run_id,
            "policy_ref": f"{run['policy_id']}:{run['policy_revision']}",
            "policy_digest": canonical_digest(self._require_policy(run["policy_id"])),
            "runtime_binding_digest": canonical_digest({"target_ref": run["target_ref"], "agents": run["agents"]}),
            "provenance": run.get("provenance", {}),
            "event_digests": [item["event_digest"] for item in events],
            "trace_digest": canonical_digest({"events": [item["event_digest"] for item in events]}),
            "trigger_manifest_digests": [
                canonical_digest(item) for item in self._store.list("sentinel_manifest", run_id=run_id)
            ],
            "control_operation_digest": canonical_digest(operation or {}),
            "event_count": len(events),
            "telemetry_schema_version": 1,
            "redaction_applied": True,
            "created_at": utc_now(),
        }
        payload["bundle_digest"] = canonical_digest(payload)
        return self._store.append("incident_bundle", bundle_id, payload, expected_revision=0)

    def overview(self, *, run_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        runs = [self._require_run(run_id)] if run_id else self._store.list("run")
        if project_id:
            normalized_project = require_token(project_id, "project_id")
            runs = [run for run in runs if run.get("project_id") == normalized_project]
        selected_run_ids = {run["run_id"] for run in runs}
        incidents = self._store.list("incident_bundle", run_id=run_id)
        controls = self._store.list("control_operation", run_id=run_id)
        if project_id and not run_id:
            incidents = [item for item in incidents if item.get("run_id") in selected_run_ids]
            controls = [item for item in controls if item.get("run_id") in selected_run_ids]
        events = [
            event
            for selected_run_id in sorted(selected_run_ids)
            for event in self._store.list_events(run_id=selected_run_id, limit=1000)
        ]
        return {
            "policies": self._store.list("policy"),
            "runs": runs,
            "incidents": incidents,
            "controls": controls,
            "events": events,
            "metrics": _safety_metrics(
                events=events,
                controls=controls,
                incidents=incidents,
                dispositions=self._store.list("incident_disposition"),
                replays=self._store.list("replay"),
            ),
            "human_intervention_required": False,
        }

    def _record_event(
        self,
        *,
        run_id: str,
        sandbox_id: str,
        agent_id: str,
        event_type: str,
        severity: str,
        source: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        policy = self._require_policy(run["policy_id"])
        trace_limit = int(policy["max_trace_events"])
        prior = self._store.list_events(run_id=run_id, limit=trace_limit)
        if len(prior) >= trace_limit:
            self.emergency_stop(run_id=run_id, action="freeze", reason="agent_safety_trace_budget_exhausted")
            raise AgentSafetyDenied("agent_safety_trace_budget_exhausted")
        event = SafetyEvent(
            event_id=f"ase_{uuid.uuid4().hex}",
            tenant_id=run["tenant_id"],
            project_id=run["project_id"],
            run_id=run_id,
            sandbox_id=require_token(sandbox_id, "sandbox_id"),
            agent_id=require_token(agent_id, "agent_id"),
            event_type=event_type,
            severity=severity,
            source=source,
            observed_at=utc_now(),
            details=details,
            previous_digest=prior[-1]["event_digest"] if prior else None,
        )
        return self._store.append_event(event.as_dict())

    def _require_policy(self, policy_id: str) -> dict[str, Any]:
        policy = self._store.get("policy", require_token(policy_id, "policy_id"))
        if not policy:
            raise KeyError("agent_safety_policy_not_found")
        return policy

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self._store.get("run", require_token(run_id, "run_id"))
        if not run:
            raise KeyError("agent_safety_run_not_found")
        return run

    def _require_active_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        if not run.get("execution_allowed") or run.get("state") != "active":
            raise AgentSafetyDenied("agent_safety_run_not_active")
        return run

    def _require_agent_binding(self, run_id: str, sandbox_id: str, agent_id: str) -> None:
        run = self._require_run(run_id)
        expected = {(str(item.get("sandbox_id") or ""), str(item.get("agent_id") or "")) for item in run["agents"]}
        binding = (require_token(sandbox_id, "sandbox_id"), require_token(agent_id, "agent_id"))
        if binding not in expected:
            raise AgentSafetyDenied("agent_safety_agent_sandbox_binding_mismatch")

    def _runs_for_scope(self, source: Mapping[str, Any], scope: StopScope) -> list[dict[str, Any]]:
        if scope in {StopScope.AGENT, StopScope.SANDBOX, StopScope.RUN}:
            return [dict(source)]
        return [
            run
            for run in self._store.list("run")
            if run.get("group_id") == source.get("group_id") and run.get("execution_allowed")
        ]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_provenance(value: Mapping[str, Any] | None) -> dict[str, str]:
    allowed = {
        "model_ref",
        "provider_ref",
        "prompt_version",
        "runtime_version",
        "sandbox_profile",
        "toolset_version",
        "training_version",
        "training_data_digest",
    }
    source = dict(value or {})
    if set(source).difference(allowed):
        raise ValueError("agent_safety_provenance_field_invalid")
    return {key: str(source[key]).strip() for key in sorted(source) if str(source[key]).strip()}


def _telemetry_details(value: Mapping[str, Any], *, high_fidelity: bool) -> dict[str, Any]:
    normalized = dict(value)
    if high_fidelity:
        return {"detail_level": "high_fidelity", **normalized}
    safe_keys = {
        "action",
        "boundary_class",
        "decision",
        "duration_ms",
        "exit_code",
        "outcome",
        "policy_id",
        "resource_class",
        "tool_name",
    }
    return {
        "detail_level": "production_minimized",
        "detail_digest": canonical_digest(normalized),
        **{key: normalized[key] for key in sorted(normalized) if key in safe_keys},
    }


def _safety_metrics(
    *,
    events: list[Mapping[str, Any]],
    controls: list[Mapping[str, Any]],
    incidents: list[Mapping[str, Any]],
    dispositions: list[Mapping[str, Any]],
    replays: list[Mapping[str, Any]],
) -> dict[str, Any]:
    boundary_outcomes: dict[str, int] = {}
    boundary_classes: dict[str, int] = {}
    self_reports = 0
    external_observations = 0
    for event in events:
        details = dict(event.get("details") or {})
        if event.get("source") == "agent_self_report":
            self_reports += 1
        else:
            external_observations += 1
        if event.get("event_type") == "boundary_crossing":
            outcome = str(details.get("outcome") or "unknown")
            boundary_class = str(details.get("boundary_class") or "unknown")
            boundary_outcomes[outcome] = boundary_outcomes.get(outcome, 0) + 1
            boundary_classes[boundary_class] = boundary_classes.get(boundary_class, 0) + 1
    receipt_failures = sum(
        1
        for control in controls
        for receipt in list(control.get("receipts") or [])
        if not bool(receipt.get("enforced"))
    )
    disposed_ids = {str(item.get("bundle_id") or "") for item in dispositions}
    replayed_ids = {str(item.get("bundle_id") or "") for item in replays}
    incident_ids = {str(item.get("bundle_id") or "") for item in incidents}
    return {
        "boundary_outcomes": dict(sorted(boundary_outcomes.items())),
        "boundary_classes": dict(sorted(boundary_classes.items())),
        "self_reports": self_reports,
        "external_observations": external_observations,
        "containment_receipt_failures": receipt_failures,
        "incident_count": len(incidents),
        "open_critical_findings": len(incident_ids.difference(disposed_ids)),
        "incident_replay_coverage": round(len(incident_ids.intersection(replayed_ids)) / len(incident_ids), 4)
        if incident_ids
        else 0.0,
    }


__all__ = ["AgentSafetyControlService", "AgentSafetyDenied"]
