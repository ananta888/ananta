from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.db_models.organizations import OrganizationLimitProfileRevisionDB
from agent.models.organization_models import (
    OrganizationBundleImportPlan,
    OrganizationBundlePlanItem,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.models.team_models import (
    OrganizationBlueprintBundleV2,
    PortableDefinitionRevision,
    PortableOrganizationInstance,
)
from agent.services.organization_bundle_apply_service import (
    OrganizationBundleApplyError,
    OrganizationBundleApplyService,
    organization_bundle_target_revision,
)
from tests.organization_support import organization_limits

_SECTION_ORDER = (
    "role_templates",
    "workflow_definitions",
    "team_blueprints",
    "handoff_definitions",
    "policies",
    "limit_profiles",
    "organization_blueprints",
)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def one_or_none(self):
        if len(self._rows) > 1:
            raise AssertionError("expected at most one row")
        return self.first()

    def one(self):
        if len(self._rows) != 1:
            raise AssertionError("expected exactly one row")
        return self._rows[0]


class _Session:
    def __init__(self, baseline_limit) -> None:
        self.baseline_limit = baseline_limit

    def exec(self, statement):
        rendered = str(statement)
        if "FROM projects" in rendered:
            return _Result(
                [
                    SimpleNamespace(
                        tenant_id="tenant-apply",
                        project_id="project-apply",
                    )
                ]
            )
        if "organization_limit_profile_revisions" in rendered:
            return _Result([self.baseline_limit])
        return _Result([])


class _AssignmentSession:
    def __init__(self, *, slots: tuple[SimpleNamespace, ...]) -> None:
        self.unit = SimpleNamespace(id="unit-one", unit_key="team-one", lifecycle="active")
        self.slots = slots
        self.assignments: list = []
        self.agent = SimpleNamespace(
            url="agent-a",
            registration_validated=True,
            status="online",
            authorized_capabilities=["code"],
            capabilities=[],
            execution_limits={"max_assignments": 4},
        )

    def get(self, _model, key):
        return self.agent if key == self.agent.url else None

    def exec(self, statement):
        rendered = str(statement)
        parameters = statement.compile().params
        if "FROM organization_units" in rendered:
            return _Result([self.unit] if parameters.get("unit_key_1") == self.unit.unit_key else [])
        if "FROM organization_role_slots" in rendered and "slot_key =" in rendered:
            slot_key = parameters.get("slot_key_1")
            return _Result([slot for slot in self.slots if slot.slot_key == slot_key])
        if "FROM organization_role_slots" in rendered:
            return _Result(list(self.slots))
        if "count(organization_role_assignments.id)" in rendered:
            rows = self._matching_assignments(parameters)
            if "role_slot_id =" in rendered:
                rows = [row for row in rows if row.role_slot_id == parameters.get("role_slot_id_1")]
            return _Result([len(rows)])
        if "FROM organization_role_assignments" in rendered:
            return _Result(self._matching_assignments(parameters))
        raise AssertionError(f"unexpected assignment query: {rendered}")

    def _matching_assignments(self, parameters: dict) -> list:
        agent_url = parameters.get("agent_url_1")
        return [row for row in self.assignments if agent_url is None or row.agent_url == agent_url]


class _AssignmentRepository:
    def __init__(self, session: _AssignmentSession) -> None:
        self._session = session

    def add(self, row):
        self._session.assignments.append(row)
        return row


class _PlanGrantService:
    def consume_in_session(self, _session, **binding):
        assert binding["grant_id"] == "bundle-grant-one"
        assert binding["grant_kind"] == "bundle_import"
        return SimpleNamespace(**binding)


class _ApplyState:
    def __init__(self) -> None:
        self.definitions: list = []
        self.operations: list = []
        self.audit_events: list = []

    def counts(self) -> tuple[int, int, int]:
        return (len(self.definitions), len(self.operations), len(self.audit_events))


class _DefinitionRepository:
    def __init__(self, rows: list, baseline_limit) -> None:
        self._rows = rows
        self._baseline = baseline_limit

    def add(self, row):
        self._rows.append(row)
        return row

    def get_limit_profile(self, tenant_id, project_id, key, version):
        if (
            tenant_id,
            project_id,
            key,
            version,
        ) == (
            self._baseline.tenant_id,
            self._baseline.project_id,
            self._baseline.policy_key,
            self._baseline.revision,
        ):
            return self._baseline
        return None

    def get_role_template(self, *_args):
        return None

    def get_team_blueprint(self, *_args):
        return None

    def get_workflow(self, *_args):
        return None

    def get_organization_blueprint(self, *_args):
        return None

    def get_handoff(self, *_args):
        return None

    def get_policy(self, *_args):
        return None


class _OperationRepository:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def add(self, row):
        for index, existing in enumerate(self._rows):
            if existing.operation_id == row.operation_id:
                self._rows[index] = row
                return row
        self._rows.append(row)
        return row

    def get_by_idempotency_key(
        self,
        tenant_id,
        project_id,
        operation_kind,
        idempotency_key,
        *,
        for_update=False,
    ):
        del for_update
        return next(
            (
                row
                for row in self._rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.operation_kind == operation_kind
                and row.idempotency_key == idempotency_key
            ),
            None,
        )


class _AuditRepository:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def add(self, row):
        self._rows.append(row)
        return row


class _TransactionalApplyUow:
    def __init__(self, state: _ApplyState, session: _Session) -> None:
        self._state = state
        self.session = session

    def __enter__(self):
        self._working = deepcopy(
            {
                "definitions": self._state.definitions,
                "operations": self._state.operations,
                "audit_events": self._state.audit_events,
            }
        )
        self.definitions = _DefinitionRepository(self._working["definitions"], self.session.baseline_limit)
        self.operations = _OperationRepository(self._working["operations"])
        self.audit_outbox = _AuditRepository(self._working["audit_events"])
        return self

    def flush(self) -> None:
        return None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_value, traceback
        if exc_type is None:
            self._state.definitions = self._working["definitions"]
            self._state.operations = self._working["operations"]
            self._state.audit_events = self._working["audit_events"]


def _portable(key: str, definition: dict) -> PortableDefinitionRevision:
    return PortableDefinitionRevision(
        key=key,
        version=1,
        lifecycle="draft",
        content_hash=canonical_definition_sha256(definition),
        definition=definition,
    )


def _bundle() -> OrganizationBlueprintBundleV2:
    imported_limits = organization_limits().model_copy(update={"policy_id": "import_limits"})
    return OrganizationBlueprintBundleV2(
        role_templates=[_portable("developer", {"prompt_template": "Execute the delegated task."})],
        workflow_definitions=[
            _portable(
                "delivery_workflow",
                {"mode": "gated", "default_failure_policy": "manual", "steps": []},
            )
        ],
        team_blueprints=[_portable("delivery_team", {"workflow_ref": "delivery_workflow@1"})],
        handoff_definitions=[
            _portable(
                "delivery_handoff",
                {"required_artifact_kinds": ["implementation"], "acceptance_gate_ref": "quality_gate@1"},
            )
        ],
        policies=[_portable("delivery_policy", {"policy_type": "routing"})],
        limit_profiles=[_portable("import_limits", imported_limits.model_dump(mode="json"))],
        organization_blueprints=[_portable("delivery_organization", {"limit_policy_ref": "baseline_limits@1"})],
    )


def _baseline_limit_row():
    limits = organization_limits().model_copy(update={"policy_id": "baseline_limits"})
    return OrganizationLimitProfileRevisionDB(
        tenant_id="tenant-apply",
        project_id="project-apply",
        policy_key=limits.policy_id,
        revision=limits.revision,
        profile_hash=limits.content_hash(),
        lifecycle="active",
        limits_json=limits.model_dump(mode="json", exclude={"policy_id", "revision"}),
    )


def _import_plan(bundle, target_revision: str) -> OrganizationBundleImportPlan:
    limits = organization_limits().model_copy(update={"policy_id": "baseline_limits"})
    items = [
        OrganizationBundlePlanItem(
            section=section,
            key=revision.key,
            version=revision.version,
            content_hash=revision.content_hash,
            action="create",
        )
        for section in _SECTION_ORDER
        for revision in getattr(bundle, section)
    ]
    payload = {
        "schema_version": "2.0",
        "tenant_id": "tenant-apply",
        "project_id": "project-apply",
        "principal_id": "organization-operator",
        "conflict_strategy": "fail",
        "bundle_digest": canonical_sha256(bundle.model_dump(mode="json")),
        "expected_target_revision": target_revision,
        "effective_limit_profile_ref": "baseline_limits@1",
        "effective_limit_profile_revision": limits.revision,
        "effective_limit_profile_hash": limits.content_hash(),
        "expires_at": "2100-01-01T00:00:00Z",
        "expires_at_epoch": 4_102_444_800.0,
        "allowed_source_refs": [],
        "allowed_run_refs": [],
        "items": [item.model_dump(mode="json") for item in items],
        "instance_plans": [],
        "instance_organization_ids": {},
        "instance_names": {},
        "instance_requested_lifecycles": {},
        "instance_admission_exception_refs": {},
        "assignment_rebindings": {},
        "errors": [],
    }
    return OrganizationBundleImportPlan(**payload, plan_digest=canonical_sha256(payload))


def _replace_plan(plan: OrganizationBundleImportPlan, **updates) -> OrganizationBundleImportPlan:
    payload = plan.model_dump(mode="json", exclude={"plan_digest"})
    payload.update(updates)
    return OrganizationBundleImportPlan(**payload, plan_digest=canonical_sha256(payload))


def _context(*, fail_at: str | None = None):
    baseline = _baseline_limit_row()
    session = _Session(baseline)
    target_revision = organization_bundle_target_revision(
        session,
        tenant_id="tenant-apply",
        project_id="project-apply",
    )
    bundle = _bundle()
    plan = _import_plan(bundle, target_revision)
    state = _ApplyState()

    def inject(step: str) -> None:
        if step == fail_at:
            raise RuntimeError(f"fault-injected:{step}")

    service = OrganizationBundleApplyService(
        limit_profiles=object(),
        uow_factory=lambda: _TransactionalApplyUow(state, session),
        fault_injector=inject,
        plan_grants=_PlanGrantService(),
    )
    return service, state, session, bundle, plan


def _apply(service, bundle, plan, **overrides):
    arguments = {
        "bundle": bundle,
        "plan": plan,
        "idempotency_key": "bundle-apply-one",
        "current_target_revision": plan.expected_target_revision,
        "tenant_id": plan.tenant_id,
        "project_id": plan.project_id,
        "principal_id": plan.principal_id,
        "admin_grant_id": "bundle-grant-one",
    }
    arguments.update(overrides)
    return service.apply(**arguments)


@pytest.mark.parametrize(
    "fault_step",
    (
        "operation",
        "role_templates:0",
        "workflow_definitions:0",
        "team_blueprints:0",
        "handoff_definitions:0",
        "policies:0",
        "limit_profiles:0",
        "organization_blueprints:0",
        "audit_outbox",
    ),
)
def test_fault_after_each_bundle_write_rolls_back_the_entire_apply(fault_step: str) -> None:
    service, state, _session, bundle, plan = _context(fail_at=fault_step)

    with pytest.raises(RuntimeError, match=f"fault-injected:{fault_step}"):
        _apply(service, bundle, plan)

    assert state.counts() == (0, 0, 0)


def test_successful_apply_and_replay_commit_one_bound_operation() -> None:
    service, state, _session, bundle, plan = _context()

    created = _apply(service, bundle, plan)
    counts_after_create = state.counts()
    replayed = _apply(service, bundle, plan)

    assert created["applied_items"] == 7
    assert created["idempotent_replay"] is False
    assert replayed["idempotent_replay"] is True
    assert replayed["operation_id"] == created["operation_id"]
    assert state.counts() == counts_after_create == (7, 1, 1)


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    (
        ("plan_digest", "organization_bundle_plan_digest_invalid"),
        ("bundle", "organization_bundle_digest_stale"),
        ("scope", "organization_bundle_scope_mismatch"),
        ("source_metadata", "organization_bundle_source_scope_metadata_forbidden"),
    ),
)
def test_tampered_bundle_plan_or_scope_fails_before_any_write(mutation: str, reason_code: str) -> None:
    service, state, _session, bundle, plan = _context()
    arguments = {}
    if mutation == "plan_digest":
        plan = plan.model_copy(update={"plan_digest": "0" * 64})
    elif mutation == "bundle":
        bundle = OrganizationBlueprintBundleV2()
    elif mutation == "scope":
        arguments["tenant_id"] = "other-tenant"
    else:
        bundle = bundle.model_copy(update={"bundle_metadata": {"project_id": "source-project-private"}})

    with pytest.raises(OrganizationBundleApplyError) as exc:
        _apply(service, bundle, plan, **arguments)

    assert exc.value.reason_code == reason_code
    assert state.counts() == (0, 0, 0)


def test_apply_rejects_source_bound_instance_before_any_write() -> None:
    service, state, _session, bundle, plan = _context()
    bundle = bundle.model_copy(
        update={
            "organization_instances": [
                PortableOrganizationInstance(
                    instance_key="source-bound-instance",
                    organization_id="source-organization-private",
                    definition_ref="delivery_organization@1",
                    definition_revision="source-definition-revision",
                    name="Source organization",
                    effective_limit_profile_ref="baseline_limits@1",
                    effective_limit_profile_revision=1,
                    effective_limit_profile_hash="source-limit-hash",
                    composition_mode="standard",
                    team_count=8,
                    plan_digest="source-plan-digest",
                    topology_snapshot={"compiled_plan": {"tenant_id": "source-tenant-private"}},
                )
            ]
        }
    )

    with pytest.raises(OrganizationBundleApplyError) as exc:
        _apply(service, bundle, plan)

    assert exc.value.reason_code == "organization_bundle_source_bound_instance_forbidden"
    assert state.counts() == (0, 0, 0)


def test_target_revision_and_limit_profile_drift_fail_closed_without_partial_state() -> None:
    service, state, session, bundle, plan = _context()

    with pytest.raises(OrganizationBundleApplyError) as stale_target:
        _apply(service, bundle, plan, current_target_revision="stale-target-revision")
    assert stale_target.value.reason_code == "organization_bundle_target_revision_stale"
    assert state.counts() == (0, 0, 0)

    drifted_plan = _replace_plan(plan, effective_limit_profile_hash="0" * 64)
    with pytest.raises(OrganizationBundleApplyError) as stale_limits:
        _apply(service, bundle, drifted_plan)
    assert stale_limits.value.reason_code == "organization_bundle_limit_profile_stale"
    assert state.counts() == (0, 0, 0)


def _assignment_slot(
    slot_key: str,
    *,
    enforcement: str = "none",
    independent_from_slot_ids: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"slot-{slot_key}",
        unit_id="unit-one",
        slot_key=slot_key,
        max_count=1,
        lifecycle="active",
        assignment_policy={
            "principal_kinds": ["agent"],
            "required_capabilities": ["code"],
            "forbidden_capabilities": [],
            "write_access_required": False,
        },
        separation_of_duties={
            "enforcement": enforcement,
            "independent_from_slot_ids": list(independent_from_slot_ids),
            "independent_from_external_duties": [],
        },
    )


def _assignment_stage_context(
    *,
    assignment_order: tuple[str, str],
    enforcement: str,
) -> tuple[_AssignmentSession, SimpleNamespace, SimpleNamespace, SimpleNamespace, dict]:
    slots = {
        "author": _assignment_slot("author"),
        "reviewer": _assignment_slot(
            "reviewer",
            enforcement=enforcement,
            independent_from_slot_ids=("author",),
        ),
    }
    session = _AssignmentSession(slots=tuple(slots.values()))
    uow = SimpleNamespace(session=session, assignments=_AssignmentRepository(session))
    principal_ref = "same-principal"
    bundle = SimpleNamespace(
        assignments=[
            SimpleNamespace(
                instance_key="instance-one",
                unit_key="team-one",
                role_slot_key=role_slot_key,
                principal_ref=principal_ref,
            )
            for role_slot_key in assignment_order
        ]
    )
    plan = SimpleNamespace(
        tenant_id="tenant-apply",
        project_id="project-apply",
        plan_digest="plan-digest",
        instance_organization_ids={"instance-one": "organization-one"},
        assignment_rebindings={principal_ref: "agent-a"},
    )
    item_by_identity = {
        (
            "assignments",
            f"instance-one:team-one:{role_slot_key}:{principal_ref}",
            1,
        ): SimpleNamespace(action="create")
        for role_slot_key in assignment_order
    }
    return session, uow, bundle, plan, item_by_identity


@pytest.mark.parametrize(
    "assignment_order",
    (("author", "reviewer"), ("reviewer", "author")),
)
def test_bundle_strict_slot_separation_blocks_both_assignment_orders(
    assignment_order: tuple[str, str],
) -> None:
    session, uow, bundle, plan, item_by_identity = _assignment_stage_context(
        assignment_order=assignment_order,
        enforcement="strict",
    )

    with pytest.raises(OrganizationBundleApplyError) as exc:
        OrganizationBundleApplyService._stage_assignments(  # noqa: SLF001 - focused apply-path contract test
            uow=uow,
            bundle=bundle,
            plan=plan,
            item_by_identity=item_by_identity,
        )

    assert exc.value.reason_code == "organization_bundle_assignment_sod_conflict"
    assert len(session.assignments) == 1


@pytest.mark.parametrize(
    "assignment_order",
    (("author", "reviewer"), ("reviewer", "author")),
)
def test_bundle_warn_slot_separation_allows_both_assignment_orders(
    assignment_order: tuple[str, str],
) -> None:
    session, uow, bundle, plan, item_by_identity = _assignment_stage_context(
        assignment_order=assignment_order,
        enforcement="warn",
    )

    applied = OrganizationBundleApplyService._stage_assignments(  # noqa: SLF001 - focused apply-path test
        uow=uow,
        bundle=bundle,
        plan=plan,
        item_by_identity=item_by_identity,
    )

    assert applied == 2
    assert len(session.assignments) == 2
