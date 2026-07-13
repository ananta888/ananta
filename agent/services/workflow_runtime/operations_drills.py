"""Deterministic production-readiness drills for workflow-runtime operations.

The drills use an isolated database and disposable in-memory key material.  They
never contact a worker or turn a runtime into a second control plane: policy,
authorization and recovery decisions remain Hub-owned.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import sqlalchemy as sa

from agent.services.workflow_authorization_grant_service import (
    SQLAlchemyWorkflowAuthorizationGrantService,
)
from agent.services.workflow_runtime._operations_rollout_support import (
    BoundDrillPerformanceEvidence,
    BoundDrillPromotionApproval,
    BoundDrillShadowComparisonEvidence,
    MissingDrillPerformanceEvidence,
    drill_evidence_key_ring,
    drill_runtime_candidates,
    drill_runtime_selector,
    drill_shadow_comparison,
    rollout_drill_plan,
    rollout_lifecycle_policy,
)
from agent.services.workflow_runtime._serialization import canonical_json, sha256_json
from agent.services.workflow_runtime.errors import SignatureValidationError
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_runtime_rollout_persistence import (
    SQLAlchemyWorkflowRolloutPolicyStore,
)
from agent.services.workflow_runtime_rollout_service import (
    AuditedWorkflowShadowPort,
    WorkflowRolloutEffectConsumer,
    WorkflowRolloutPolicy,
    WorkflowRolloutPolicyService,
    WorkflowRolloutScope,
    WorkflowRuntimePromotionService,
    WorkflowRuntimeRollbackService,
    WorkflowShadowIntent,
)
from agent.services.workflow_runtime_selection_service import (
    InMemoryRuntimeSelectionAudit,
    RuntimeCandidate,
)

OPERATIONS_DRILL_REPORT_SCHEMA = "ananta.workflow_runtime_operations_drill.v1"
_TRACKED_TABLES = (
    "alembic_version",
    "workflow_authorization_grants",
    "workflow_provider_budgets",
    "workflow_runtime_rollout_audit",
    "workflow_runtime_rollout_policies",
)


@dataclass(frozen=True)
class OperationsDrillResult:
    drill_id: str
    invariants: tuple[str, ...]
    evidence: dict[str, Any]
    status: str = "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "drill_id": self.drill_id,
            "status": self.status,
            "invariants": list(self.invariants),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class WorkflowRuntimeOperationsDrillReport:
    source_revision: str
    results: tuple[OperationsDrillResult, ...]
    schema: str = OPERATIONS_DRILL_REPORT_SCHEMA

    @property
    def status(self) -> str:
        return "passed" if self.results and all(result.status == "passed" for result in self.results) else "failed"

    @property
    def evidence_id(self) -> str:
        return "wrod-" + sha256_json(self._content())

    def _content(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_revision": self.source_revision,
            "status": self.status,
            "results": [result.to_dict() for result in self.results],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content(), "evidence_id": self.evidence_id}


class MigrationCommandPort(Protocol):
    def run(self, *arguments: str) -> None: ...


class AlembicSubprocessRunner:
    """Run repository Alembic commands against one explicitly isolated DB."""

    def __init__(self, *, repository_root: Path, database: Path, data_dir: Path):
        self._root = repository_root.resolve()
        self._database = database.resolve()
        self._data_dir = data_dir.resolve()

    def run(self, *arguments: str) -> None:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": f"sqlite:///{self._database}",
                "DATA_DIR": str(self._data_dir),
                "DISABLE_INITIAL_ADMIN": "true",
                "ROLE": "hub",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(self._root / "alembic.ini"),
                *arguments,
            ],
            cwd=self._root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError("workflow_operations_alembic_command_failed")


@dataclass(frozen=True)
class MigratedDatabase:
    path: Path
    head_revision: str
    previous_revision: str
    head_tables: tuple[str, ...]
    head_schema_digest: str


class AlembicUpgradeDowngradeDrill:
    """Exercise clean upgrade, one-revision downgrade and N-1 re-upgrade."""

    def __init__(self, runner: MigrationCommandPort):
        self._runner = runner

    def run(self, database: Path) -> tuple[MigratedDatabase, OperationsDrillResult]:
        self._runner.run("upgrade", "head")
        head_revision = _database_revision(database)
        head_tables = _table_names(database)
        head_schema_digest = _schema_digest(database)
        _require_runtime_tables(head_tables)
        _seed_n_minus_one_row(database)

        self._runner.run("downgrade", "-1")
        previous_revision = _database_revision(database)
        if previous_revision == head_revision or _schema_digest(database) == head_schema_digest:
            raise RuntimeError("workflow_operations_n_minus_one_not_exercised")
        if _provider_budget_count(database) != 1:
            raise RuntimeError("workflow_operations_n_minus_one_data_lost")

        self._runner.run("upgrade", "head")
        if _database_revision(database) != head_revision:
            raise RuntimeError("workflow_operations_head_revision_not_restored")
        if _table_names(database) != head_tables or _schema_digest(database) != head_schema_digest:
            raise RuntimeError("workflow_operations_head_schema_not_restored")
        if _provider_budget_count(database) != 1:
            raise RuntimeError("workflow_operations_reupgrade_data_lost")

        result = OperationsDrillResult(
            drill_id="alembic-upgrade-downgrade-n-minus-one",
            invariants=(
                "single_head_reached",
                "real_downgrade_removed_head_schema",
                "n_minus_one_data_survived",
                "reupgrade_restored_exact_head_schema",
            ),
            evidence={
                "head_revision": head_revision,
                "previous_revision": previous_revision,
                "runtime_table_count": len([name for name in head_tables if name.startswith("workflow_")]),
                "preserved_rows": 1,
            },
        )
        return MigratedDatabase(
            path=database,
            head_revision=head_revision,
            previous_revision=previous_revision,
            head_tables=head_tables,
            head_schema_digest=head_schema_digest,
        ), result


@dataclass(frozen=True)
class SeededHubState:
    envelope: RuntimeAuthorizationEnvelope
    logical_digest: str


@dataclass(frozen=True)
class RestoredHubState:
    database: Path
    envelope: RuntimeAuthorizationEnvelope


class SQLiteBackupRestoreDrill:
    """Back up and restore Hub state through SQLite's consistent backup API."""

    def run(self, migrated: MigratedDatabase, *, workspace: Path) -> tuple[RestoredHubState, OperationsDrillResult]:
        seeded = _seed_hub_state(migrated.path)
        backup = workspace / "workflow-runtime.backup.sqlite"
        restored = workspace / "workflow-runtime.restored.sqlite"
        _sqlite_backup(migrated.path, backup)
        backup.chmod(0o600)
        if stat.S_IMODE(backup.stat().st_mode) != 0o600:
            raise RuntimeError("workflow_operations_backup_permissions_invalid")
        if _integrity_check(backup) != "ok":
            raise RuntimeError("workflow_operations_backup_integrity_failed")
        if _logical_digest(backup) != seeded.logical_digest:
            raise RuntimeError("workflow_operations_backup_digest_mismatch")

        _mutate_source_after_backup(migrated.path, seeded.envelope)
        if _logical_digest(migrated.path) == seeded.logical_digest:
            raise RuntimeError("workflow_operations_backup_mutation_not_observed")
        _sqlite_backup(backup, restored)
        if _integrity_check(restored) != "ok":
            raise RuntimeError("workflow_operations_restore_integrity_failed")
        if _logical_digest(restored) != seeded.logical_digest:
            raise RuntimeError("workflow_operations_restore_digest_mismatch")
        _verify_restored_hub_state(restored, seeded.envelope, migrated.head_revision)

        result = OperationsDrillResult(
            drill_id="database-backup-restore",
            invariants=(
                "backup_integrity_ok",
                "backup_is_owner_only",
                "source_mutation_did_not_change_backup",
                "schema_policy_audit_budget_and_grant_restored",
            ),
            evidence={
                "schema_revision": migrated.head_revision,
                "logical_digest": seeded.logical_digest,
                "tracked_table_count": len(_TRACKED_TABLES),
                "backup_mode": "0600",
            },
        )
        return RestoredHubState(restored, seeded.envelope), result


class AuthorizationKeyRotationDrill:
    """Exercise overlap, cutover and old-key revocation without key output."""

    def run(self) -> OperationsDrillResult:
        ring = _disposable_key_ring("rotation-old")
        old = _issue_envelope(ring, envelope_id="rotation-old-envelope", now=100.0)
        _verify_envelope(old, ring, now=110.0)
        ring.rotate(key_id="rotation-new", key=_derived_key("rotation-new"))
        _verify_envelope(old, ring, now=111.0)
        new = _issue_envelope(ring, envelope_id="rotation-new-envelope", now=112.0)
        _verify_envelope(new, ring, now=113.0)
        ring.revoke_key("rotation-old")
        try:
            _verify_envelope(old, ring, now=114.0)
        except SignatureValidationError as exc:
            if "revoked" not in str(exc):
                raise
        else:
            raise RuntimeError("workflow_operations_revoked_key_accepted")
        _verify_envelope(new, ring, now=114.0)
        return OperationsDrillResult(
            drill_id="authorization-key-rotation",
            invariants=(
                "old_contract_valid_during_overlap",
                "new_contract_uses_new_active_key",
                "old_key_rejected_after_revocation",
                "new_key_remains_valid",
            ),
            evidence={
                "old_key_id": "rotation-old",
                "active_key_id": ring.active_key_id,
                "secret_material_emitted": False,
            },
        )


class IncidentContainmentRecoveryDrill:
    """Contain a scope, revoke authority and recover only into safe shadow."""

    def run(self, restored: RestoredHubState) -> OperationsDrillResult:
        engine = _engine(restored.database)
        policies = WorkflowRolloutPolicyService(SQLAlchemyWorkflowRolloutPolicyStore(engine), clock=lambda: 300.0)
        grants = SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: 300.0)
        scope = WorkflowRolloutScope(project_id="project-drill")
        contained = _policy(scope, version="incident-contained-v1", mode="disabled")
        policies.set_policy(
            contained,
            expected_revision=1,
            actor_id="hub-incident-controller",
            reason_code="suspected_authorization_exposure",
            change_id="incident-contain-change",
            action="incident_contained",
        )
        revoked = grants.revoke(
            restored.envelope.envelope_id,
            reason_code="incident_authorization_revoked",
            expected_revision=1,
        )
        if policies.resolve(scope).policy.mode != "disabled":
            raise RuntimeError("workflow_operations_incident_scope_not_disabled")
        if grants.revalidate(restored.envelope):
            raise RuntimeError("workflow_operations_incident_grant_still_active")

        recovered = _policy(scope, version="incident-recovery-v1", mode="shadow")
        policies.set_policy(
            recovered,
            expected_revision=2,
            actor_id="hub-incident-controller",
            reason_code="restore_and_security_gates_passed",
            change_id="incident-recovery-change",
            action="incident_recovery_staged",
        )
        ring = _disposable_key_ring("recovery-active")
        fresh = _issue_envelope(
            ring,
            envelope_id="incident-recovery-envelope",
            now=250.0,
        )
        grants.grant(fresh)
        if not grants.revalidate(fresh) or grants.revalidate(restored.envelope):
            raise RuntimeError("workflow_operations_recovery_grant_state_invalid")

        decision = WorkflowRolloutEffectConsumer(
            policies=policies,
            shadow=AuditedWorkflowShadowPort(policies.store, clock=lambda: 301.0),
        ).evaluate(
            WorkflowShadowIntent(
                intent_id="incident-shadow-write",
                scope=scope,
                tenant_id="tenant-drill",
                workflow_id="workflow-drill",
                run_id="run-drill",
                step_id="step-drill",
                intent_type="write",
                side_effect_class="idempotent_write",
                payload_digest=sha256_json({"intent": "incident-shadow-write"}),
            )
        )
        actions = {event.action for event in policies.store.list_audit(scope)}
        required_actions = {
            "incident_contained",
            "incident_recovery_staged",
            "shadow_intent_suppressed",
        }
        if not decision.suppressed or not required_actions.issubset(actions):
            raise RuntimeError("workflow_operations_incident_audit_incomplete")
        engine.dispose()
        return OperationsDrillResult(
            drill_id="incident-containment-recovery",
            invariants=(
                "scope_disabled_before_recovery",
                "exposed_grant_revoked",
                "recovery_staged_in_shadow_not_live",
                "shadow_write_suppressed_and_audited",
            ),
            evidence={
                "revoked_grant_revision": revoked.revision,
                "recovery_mode": "shadow",
                "audit_actions": sorted(required_actions),
                "fresh_grant_active": True,
                "old_grant_active": False,
            },
        )


class RolloutLifecycleDrill:
    """Exercise Hub-owned disabled, shadow, promotion and rollback transitions."""

    def __init__(
        self,
        policies: WorkflowRolloutPolicyService,
        *,
        candidates: tuple[RuntimeCandidate, ...] | None = None,
    ) -> None:
        self._policies = policies
        self._candidates = drill_runtime_candidates() if candidates is None else candidates

    def run(self, *, source_revision: str) -> OperationsDrillResult:
        revision = str(source_revision).strip()
        if not revision:
            raise ValueError("workflow_operations_source_revision_required")
        scope = WorkflowRolloutScope(project_id="project-rollout-drill")
        disabled = self._policies.set_policy(
            rollout_lifecycle_policy(scope, version="disabled-v1", mode="disabled"),
            expected_revision=0,
            actor_id="hub-release-controller",
            reason_code="staging_default_disabled",
            change_id="rollout-disabled-change",
            action="staging_disabled",
        )
        shadow = self._policies.set_policy(
            rollout_lifecycle_policy(scope, version="shadow-v1", mode="shadow"),
            expected_revision=disabled.revision,
            actor_id="hub-release-controller",
            reason_code="staging_shadow_authorized",
            change_id="rollout-shadow-change",
            action="staging_shadow_enabled",
        )
        effect_consumer = WorkflowRolloutEffectConsumer(
            policies=self._policies,
            shadow=AuditedWorkflowShadowPort(
                self._policies.store,
                clock=lambda: 401.0,
            ),
        )
        decisions = tuple(
            effect_consumer.evaluate(intent)
            for intent in (
                WorkflowShadowIntent(
                    intent_id="rollout-shadow-write",
                    scope=scope,
                    tenant_id="tenant-drill",
                    workflow_id="workflow-rollout-drill",
                    run_id="run-rollout-drill",
                    step_id="step-rollout-drill",
                    intent_type="write",
                    side_effect_class="idempotent_write",
                    payload_digest=sha256_json({"intent": "shadow-write"}),
                ),
                WorkflowShadowIntent(
                    intent_id="rollout-shadow-egress",
                    scope=scope,
                    tenant_id="tenant-drill",
                    workflow_id="workflow-rollout-drill",
                    run_id="run-rollout-drill",
                    step_id="step-rollout-drill",
                    intent_type="egress",
                    side_effect_class="read",
                    target="https://blocked.invalid",
                    payload_digest=sha256_json({"intent": "shadow-egress"}),
                ),
            )
        )
        if not all(decision.suppressed and not decision.allowed for decision in decisions):
            raise RuntimeError("workflow_operations_shadow_effect_not_suppressed")
        shadow_comparison = drill_shadow_comparison(source_revision=revision)
        drifted_shadow_comparison = drill_shadow_comparison(
            source_revision=revision,
            semantic_drift=True,
        )

        selection_audit = InMemoryRuntimeSelectionAudit()
        selection = drill_runtime_selector(self._candidates, selection_audit)
        plan = rollout_drill_plan()
        live_policy = rollout_lifecycle_policy(
            scope,
            version="live-v1",
            mode="live",
        )
        performance = BoundDrillPerformanceEvidence(
            source_revision=revision,
            shadow_audit_refs=tuple(decision.audit_ref for decision in decisions),
            shadow_comparison_ref=shadow_comparison.evidence_ref,
        )
        evidence_keys = drill_evidence_key_ring()
        approval = BoundDrillPromotionApproval(
            policy=live_policy,
            plan=plan,
            revision=shadow.revision,
        )
        promotion = WorkflowRuntimePromotionService(
            policies=self._policies,
            selection=selection,
            performance=performance,
            shadow_comparison=BoundDrillShadowComparisonEvidence(
                shadow_comparison,
                key_ring=evidence_keys,
            ),
            approval=approval,
            evidence_keys=evidence_keys,
            expected_source_revision=revision,
            clock=lambda: 451.0,
        )
        try:
            promotion.promote(
                policy=live_policy,
                plan=plan,
                expected_revision=shadow.revision,
                actor_id="hub-release-controller",
                reason_code="approval_must_be_present",
                change_id="rollout-promotion-without-approval",
                approval_id="",
            )
        except ValueError as exc:
            if "promotion_approval_required" not in str(exc):
                raise
        else:
            raise RuntimeError("workflow_operations_approval_gate_bypassed")

        missing_evidence_promotion = WorkflowRuntimePromotionService(
            policies=self._policies,
            selection=selection,
            performance=MissingDrillPerformanceEvidence(),
            shadow_comparison=BoundDrillShadowComparisonEvidence(
                shadow_comparison,
                key_ring=evidence_keys,
            ),
            approval=approval,
            evidence_keys=evidence_keys,
            expected_source_revision=revision,
            clock=lambda: 451.0,
        )
        try:
            missing_evidence_promotion.promote(
                policy=live_policy,
                plan=plan,
                expected_revision=shadow.revision,
                actor_id="hub-release-controller",
                reason_code="evidence_must_be_present",
                change_id="rollout-promotion-without-evidence",
                approval_id="approval-rollout-promotion-without-evidence",
            )
        except RuntimeError as exc:
            if "promotion_evidence_unavailable" not in str(exc):
                raise
        else:
            raise RuntimeError("workflow_operations_evidence_gate_bypassed")
        current = self._policies.store.get(scope)
        if current is None or current.revision != shadow.revision:
            raise RuntimeError("workflow_operations_failed_promotion_changed_policy")

        unsafe_shadow_promotion = WorkflowRuntimePromotionService(
            policies=self._policies,
            selection=selection,
            performance=performance,
            shadow_comparison=BoundDrillShadowComparisonEvidence(
                drifted_shadow_comparison,
                key_ring=evidence_keys,
            ),
            approval=approval,
            evidence_keys=evidence_keys,
            expected_source_revision=revision,
            clock=lambda: 451.0,
        )
        try:
            unsafe_shadow_promotion.promote(
                policy=live_policy,
                plan=plan,
                expected_revision=shadow.revision,
                actor_id="hub-release-controller",
                reason_code="shadow_result_must_match",
                change_id="rollout-promotion-with-drift",
                approval_id="approval-rollout-promotion-with-drift",
            )
        except RuntimeError as exc:
            if "workflow_shadow_comparison_failed" not in str(exc):
                raise
        else:
            raise RuntimeError("workflow_operations_shadow_comparison_gate_bypassed")

        promoted = promotion.promote(
            policy=live_policy,
            plan=plan,
            expected_revision=shadow.revision,
            actor_id="hub-release-controller",
            reason_code="shadow_and_performance_evidence_passed",
            change_id="rollout-promotion-change",
            approval_id="approval-rollout-promotion-change",
        )
        approval_bound = "approval:approval-rollout-promotion-change" in (promoted.stored_policy.policy.evidence_refs)
        evidence_bound = promoted.performance_evidence.evidence_ref in promoted.stored_policy.policy.evidence_refs
        selection_bound = promoted.runtime_selection.audit_ref in promoted.stored_policy.policy.evidence_refs
        if not approval_bound or not evidence_bound or not selection_bound:
            raise RuntimeError("workflow_operations_promotion_binding_incomplete")

        rollback = WorkflowRuntimeRollbackService(
            policies=self._policies,
            selection=selection,
        ).rollback(
            scope=scope,
            plan=plan,
            target_runtime="langgraph",
            policy_version="rollback-v1",
            expected_revision=promoted.stored_policy.revision,
            actor_id="hub-release-controller",
            reason_code="staging_rollback_rehearsal",
            change_id="rollout-rollback-change",
            evidence_refs=("operations-drill-rollback-evidence",),
        )
        required = set(rollback.stored_policy.policy.required_capabilities)
        if required - set(rollback.runtime_selection.capabilities):
            raise RuntimeError("workflow_operations_rollback_capability_loss")
        rollback_selection_bound = rollback.runtime_selection.audit_ref in rollback.stored_policy.policy.evidence_refs
        if not rollback_selection_bound:
            raise RuntimeError("workflow_operations_rollback_evidence_incomplete")
        actions = {event.action for event in self._policies.store.list_audit(scope)}
        required_actions = {
            "staging_disabled",
            "staging_shadow_enabled",
            "shadow_intent_suppressed",
            "performance_safe_promotion",
            "capability_safe_rollback",
        }
        if not required_actions.issubset(actions):
            raise RuntimeError("workflow_operations_rollout_audit_incomplete")
        return OperationsDrillResult(
            drill_id="rollout-lifecycle-promotion-rollback",
            invariants=(
                "disabled_baseline_preceded_shadow",
                "shadow_write_and_egress_suppressed",
                "shadow_result_matched_canonical_baseline_invariants",
                "live_promotion_rejected_without_approval_or_evidence",
                "live_promotion_rejected_on_shadow_result_drift",
                "successful_promotion_bound_approval_evidence_and_selection",
                "rollback_target_preserved_all_required_capabilities",
            ),
            evidence={
                "policy_modes": ["disabled", "shadow", "live", "live"],
                "policy_revisions": [
                    disabled.revision,
                    shadow.revision,
                    promoted.stored_policy.revision,
                    rollback.stored_policy.revision,
                ],
                "shadow_suppressed_intents": len(decisions),
                "shadow_comparison_status": shadow_comparison.status,
                "shadow_comparison_ref": shadow_comparison.evidence_ref,
                "approval_gate_rejection_observed": True,
                "evidence_gate_rejection_observed": True,
                "shadow_drift_rejection_observed": True,
                "approval_reference_bound": approval_bound,
                "performance_evidence_bound": evidence_bound,
                "selection_evidence_bound": selection_bound,
                "rollback_selection_evidence_bound": rollback_selection_bound,
                "promoted_runtime": promoted.runtime_selection.runtime_id,
                "rollback_runtime": rollback.runtime_selection.runtime_id,
                "rollback_required_capabilities": sorted(required),
                "selection_attempt_count": len(selection_audit.records),
                "audit_actions": sorted(required_actions),
            },
        )


class WorkflowRuntimeOperationsDrillSuite:
    """Compose the five release-blocking AIR-055 operations drills."""

    def __init__(self, *, repository_root: Path):
        self._root = repository_root.resolve()

    def run(self, *, workspace: Path, source_revision: str) -> WorkflowRuntimeOperationsDrillReport:
        workspace = workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=False)
        data_dir = workspace / "migration-data"
        data_dir.mkdir(mode=0o700)
        database = workspace / "workflow-runtime.sqlite"
        migration = AlembicUpgradeDowngradeDrill(
            AlembicSubprocessRunner(
                repository_root=self._root,
                database=database,
                data_dir=data_dir,
            )
        )
        migrated, migration_result = migration.run(database)
        restored, backup_result = SQLiteBackupRestoreDrill().run(migrated, workspace=workspace)
        rotation_result = AuthorizationKeyRotationDrill().run()
        incident_result = IncidentContainmentRecoveryDrill().run(restored)
        rollout_engine = _engine(restored.database)
        try:
            rollout_result = RolloutLifecycleDrill(
                WorkflowRolloutPolicyService(
                    SQLAlchemyWorkflowRolloutPolicyStore(rollout_engine),
                    clock=lambda: 400.0,
                )
            ).run(source_revision=source_revision)
        finally:
            rollout_engine.dispose()
        report = WorkflowRuntimeOperationsDrillReport(
            source_revision=str(source_revision).strip(),
            results=(
                migration_result,
                backup_result,
                rotation_result,
                incident_result,
                rollout_result,
            ),
        )
        if not report.source_revision:
            raise ValueError("workflow_operations_source_revision_required")
        if report.status != "passed":
            raise RuntimeError("workflow_operations_drill_failed")
        return report


def _engine(path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{path.resolve()}")


def _database_revision(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num").fetchall()
    if len(rows) != 1 or not str(rows[0][0]).strip():
        raise RuntimeError("workflow_operations_single_alembic_head_required")
    return str(rows[0][0])


def _table_names(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return tuple(str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_"))


def _schema_digest(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '')
            FROM sqlite_master
            WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name, tbl_name, sql
            """
        ).fetchall()
    return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()


def _require_runtime_tables(tables: tuple[str, ...]) -> None:
    missing = set(_TRACKED_TABLES) - set(tables)
    if missing:
        raise RuntimeError("workflow_operations_runtime_schema_incomplete")


def _seed_n_minus_one_row(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_provider_budgets (
                id, tenant_id, run_id, policy_version, attempts, tokens,
                cost_micros, maximum_attempts, maximum_tokens,
                maximum_cost_micros, revision, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "budget-drill",
                "tenant-drill",
                "run-drill",
                "policy-drill",
                0,
                0,
                0,
                3,
                1000,
                500,
                1,
                100.0,
            ),
        )


def _provider_budget_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM workflow_provider_budgets").fetchone()[0])


def _seed_hub_state(path: Path) -> SeededHubState:
    engine = _engine(path)
    policies = WorkflowRolloutPolicyService(SQLAlchemyWorkflowRolloutPolicyStore(engine), clock=lambda: 200.0)
    scope = WorkflowRolloutScope(project_id="project-drill")
    policies.set_policy(
        _policy(scope, version="staging-shadow-v1", mode="shadow"),
        expected_revision=0,
        actor_id="hub-release-controller",
        reason_code="air055_staging_baseline",
        change_id="staging-baseline-change",
        action="staging_baseline_created",
    )
    ring = _disposable_key_ring("backup-active")
    envelope = _issue_envelope(
        ring,
        envelope_id="backup-active-envelope",
        now=100.0,
    )
    SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: 200.0).grant(envelope)
    engine.dispose()
    return SeededHubState(envelope=envelope, logical_digest=_logical_digest(path))


def _policy(scope: WorkflowRolloutScope, *, version: str, mode: str) -> WorkflowRolloutPolicy:
    active = mode in {"shadow", "live", "drain"}
    return WorkflowRolloutPolicy(
        scope=scope,
        policy_version=version,
        mode=mode,
        preferred_runtime="ananta-native" if mode in {"shadow", "live"} else "",
        allowed_runtimes=("ananta-native", "langgraph", "temporal") if active else (),
        required_capabilities=(
            "audit",
            "authorization",
            "policy",
            "side_effect_guard",
        ),
        allowed_side_effect_classes=("none", "read"),
        fallback_semantics="none",
        evidence_refs=("operations-drill",),
    )


def _mutate_source_after_backup(path: Path, envelope: RuntimeAuthorizationEnvelope) -> None:
    engine = _engine(path)
    SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: 201.0).revoke(
        envelope.envelope_id,
        reason_code="post_backup_mutation",
        expected_revision=1,
    )
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM workflow_provider_budgets WHERE id = :id"),
            {"id": "budget-drill"},
        )
    engine.dispose()


def _verify_restored_hub_state(path: Path, envelope: RuntimeAuthorizationEnvelope, expected_revision: str) -> None:
    if _database_revision(path) != expected_revision or _provider_budget_count(path) != 1:
        raise RuntimeError("workflow_operations_restore_database_invariant_failed")
    engine = _engine(path)
    policies = WorkflowRolloutPolicyService(SQLAlchemyWorkflowRolloutPolicyStore(engine))
    grants = SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: 200.0)
    policy = policies.resolve(WorkflowRolloutScope(project_id="project-drill"))
    audit = policies.store.list_audit(WorkflowRolloutScope(project_id="project-drill"))
    if policy.policy.mode != "shadow" or policy.revisions != (("project", 1),):
        raise RuntimeError("workflow_operations_restore_policy_invariant_failed")
    if len(audit) != 1 or not grants.revalidate(envelope):
        raise RuntimeError("workflow_operations_restore_authority_invariant_failed")
    engine.dispose()


def _sqlite_backup(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)


def _integrity_check(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _logical_digest(path: Path) -> str:
    snapshot: dict[str, Any] = {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = set(_table_names(path))
        for table in _TRACKED_TABLES:
            if table not in tables:
                raise RuntimeError("workflow_operations_snapshot_table_missing")
            columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
            order = ", ".join(f'"{column}"' for column in columns)
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()
            snapshot[table] = [{column: _normalize_sql_value(row[column]) for column in columns} for row in rows]
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def _normalize_sql_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _derived_key(label: str) -> bytes:
    return hashlib.sha256(f"disposable-air055:{label}".encode("utf-8")).digest()


def _disposable_key_ring(key_id: str) -> HmacKeyRing:
    return HmacKeyRing({key_id: _derived_key(key_id)}, active_key_id=key_id)


def _issue_envelope(ring: HmacKeyRing, *, envelope_id: str, now: float) -> RuntimeAuthorizationEnvelope:
    return RuntimeAuthorizationEnvelope.issue(
        key_ring=ring,
        tenant_id="tenant-drill",
        workflow_id="workflow-drill",
        run_id="run-drill",
        step_id="step-drill",
        plan_hash="d" * 64,
        policy_version="policy-drill",
        allowed_tools=("read-only-probe",),
        budgets={"tokens": 100},
        ttl_seconds=1_000.0,
        now=now,
        envelope_id=envelope_id,
        nonce=f"{envelope_id}-nonce",
    )


def _verify_envelope(envelope: RuntimeAuthorizationEnvelope, ring: HmacKeyRing, *, now: float) -> None:
    envelope.verify(
        key_ring=ring,
        tenant_id="tenant-drill",
        workflow_id="workflow-drill",
        run_id="run-drill",
        step_id="step-drill",
        plan_hash="d" * 64,
        policy_version="policy-drill",
        now=now,
    )


__all__ = [
    "AlembicSubprocessRunner",
    "AlembicUpgradeDowngradeDrill",
    "AuthorizationKeyRotationDrill",
    "IncidentContainmentRecoveryDrill",
    "OperationsDrillResult",
    "RolloutLifecycleDrill",
    "SQLiteBackupRestoreDrill",
    "WorkflowRuntimeOperationsDrillReport",
    "WorkflowRuntimeOperationsDrillSuite",
]
