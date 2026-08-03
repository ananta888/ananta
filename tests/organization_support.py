from __future__ import annotations

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    OrganizationLimitProfile,
    OrganizationRelationDefinition,
    OrganizationUnitDefinition,
    OrganizationUnitGroupDefinition,
    RoleSlotDefinition,
    StandardCompositionDefinition,
    TeamBlueprintDefinition,
)
from agent.services.organization_blueprint_compiler import OrganizationBlueprintCompiler

TEAM_KEYS = (
    "enterprise_product_delivery_scrum",
    "portfolio_product_coordination",
    "research_and_discovery",
    "proof_of_concept",
    "platform_devops_sre",
    "architecture_governance",
    "quality_security_release",
)


def organization_limits() -> OrganizationLimitProfile:
    return OrganizationLimitProfile(
        policy_id="organization_limits",
        revision=1,
        max_team_instances_per_organization=32,
        max_units_per_organization=128,
        max_role_slots_per_organization=1024,
        max_assignments_per_organization=2048,
        max_relations_per_organization=4096,
        max_workflow_steps_per_organization=512,
        max_bundle_bytes=10_485_760,
        max_patch_operations=100,
        topology_default_page_size=100,
        topology_max_page_size=500,
        topology_max_depth=8,
        runtime_overlay_max_events=2000,
        canvas_render_node_limit=500,
        canvas_render_edge_limit=2000,
    )


def organization_definition() -> OrganizationBlueprintDefinition:
    structural = [
        OrganizationUnitDefinition(
            unit_key="enterprise_portfolio",
            unit_kind="coordination_unit",
            materialization_kind="structural_unit",
            activation_policy="always",
        ),
        OrganizationUnitDefinition(
            unit_key="discovery_value_stream",
            unit_kind="value_stream",
            materialization_kind="structural_unit",
            parent_unit_ref="enterprise_portfolio",
            activation_policy="always",
        ),
        OrganizationUnitDefinition(
            unit_key="delivery_value_stream",
            unit_kind="value_stream",
            materialization_kind="structural_unit",
            parent_unit_ref="enterprise_portfolio",
            activation_policy="always",
        ),
        OrganizationUnitDefinition(
            unit_key="enablement_value_stream",
            unit_kind="value_stream",
            materialization_kind="structural_unit",
            parent_unit_ref="enterprise_portfolio",
            activation_policy="always",
        ),
    ]
    team_units = [
        ("portfolio_product_coordination", "enterprise_portfolio", "baseline"),
        ("research_and_discovery", "discovery_value_stream", "baseline"),
        ("proof_of_concept", "discovery_value_stream", "ordered_optional"),
        ("platform_devops_sre", "enablement_value_stream", "baseline"),
        ("quality_security_release", "enablement_value_stream", "ordered_optional"),
        ("architecture_governance", "enablement_value_stream", "ordered_optional"),
    ]
    return OrganizationBlueprintDefinition(
        key="enterprise_scrum_organization",
        version=1,
        description="Enterprise organization test definition.",
        parameter_schema={
            "type": "object",
            "additionalProperties": False,
            "discriminator": "composition_mode",
            "oneOf": [{"composition_mode": "standard"}, {"composition_mode": "custom"}],
        },
        standard_composition=StandardCompositionDefinition(
            team_count_range={"minimum": 5, "default": 8, "maximum": 10},
            baseline_singleton_team_refs=[
                "portfolio_product_coordination",
                "research_and_discovery",
                "platform_devops_sre",
            ],
            baseline_group_counts={"product_delivery": 2},
            activation_order=[
                "quality_security_release",
                "architecture_governance",
                "proof_of_concept",
            ],
            scale_out_group="product_delivery",
        ),
        unit_groups=[
            OrganizationUnitGroupDefinition(
                group_id="product_delivery",
                team_blueprint_ref="enterprise_product_delivery_scrum@1",
                parent_unit_ref="delivery_value_stream",
                min_count=1,
                default_count=2,
                max_count=None,
                capacity_rule={
                    "formula": "effective_max_team_instances_minus_active_singleton_teams",
                    "minimum_remaining": 1,
                },
                naming_policy="stable_group_ordinal",
                limit_policy_ref="organization_limits@1",
                overrides={},
            )
        ],
        units=[
            *structural,
            *[
                OrganizationUnitDefinition(
                    unit_key=key,
                    unit_kind="team",
                    materialization_kind="team_instance",
                    parent_unit_ref=parent,
                    team_blueprint_ref=f"{key}@1",
                    activation_policy=activation,
                )
                for key, parent, activation in team_units
            ],
        ],
        relations=[
            OrganizationRelationDefinition(
                relation_id="portfolio_governs_discovery",
                namespace="organization",
                source_unit_ref="portfolio_product_coordination",
                target_unit_ref="discovery_value_stream",
                kind="governs",
                activation_condition="both_endpoints_materialized",
                handoff_contract_ref=None,
                dependency_policy="advisory",
                escalation_policy="portfolio",
            ),
            OrganizationRelationDefinition(
                relation_id="portfolio_governs_delivery",
                namespace="organization",
                source_unit_ref="portfolio_product_coordination",
                target_unit_ref="delivery_value_stream",
                kind="governs",
                activation_condition="both_endpoints_materialized",
                handoff_contract_ref=None,
                dependency_policy="advisory",
                escalation_policy="portfolio",
            ),
            OrganizationRelationDefinition(
                relation_id="portfolio_governs_enablement",
                namespace="organization",
                source_unit_ref="portfolio_product_coordination",
                target_unit_ref="enablement_value_stream",
                kind="governs",
                activation_condition="both_endpoints_materialized",
                handoff_contract_ref=None,
                dependency_policy="advisory",
                escalation_policy="portfolio",
            ),
            OrganizationRelationDefinition(
                relation_id="research_supplies_poc",
                namespace="organization",
                source_unit_ref="research_and_discovery",
                target_unit_ref="proof_of_concept",
                kind="supplies_research_to",
                activation_condition="both_endpoints_materialized",
                dependency_policy="declared",
                handoff_contract_ref="research_handoff@1",
                escalation_policy="portfolio",
            ),
            OrganizationRelationDefinition(
                relation_id="poc_prototypes_for_delivery",
                namespace="organization",
                source_unit_ref="proof_of_concept",
                target_unit_ref="delivery_value_stream",
                kind="prototypes_for",
                activation_condition="both_endpoints_materialized",
                dependency_policy="declared",
                handoff_contract_ref="poc_handoff@1",
                escalation_policy="portfolio",
            ),
            OrganizationRelationDefinition(
                relation_id="architecture_reviews_delivery",
                namespace="organization",
                source_unit_ref="architecture_governance",
                target_unit_ref="delivery_value_stream",
                kind="reviews",
                activation_condition="both_endpoints_materialized",
                dependency_policy="gate",
                handoff_contract_ref="architecture_handoff@1",
                escalation_policy="architecture",
            ),
            OrganizationRelationDefinition(
                relation_id="platform_enables_delivery",
                namespace="organization",
                source_unit_ref="platform_devops_sre",
                target_unit_ref="delivery_value_stream",
                kind="enables",
                activation_condition="both_endpoints_materialized",
                dependency_policy="declared",
                handoff_contract_ref="platform_handoff@1",
                escalation_policy="operations",
            ),
            OrganizationRelationDefinition(
                relation_id="quality_releases_delivery",
                namespace="organization",
                source_unit_ref="quality_security_release",
                target_unit_ref="delivery_value_stream",
                kind="releases_for",
                activation_condition="both_endpoints_materialized",
                dependency_policy="gate",
                handoff_contract_ref="release_handoff@1",
                escalation_policy="release",
            ),
        ],
        shared_product_model={"goal_scope": "organization", "team_backlogs": "derived"},
        orchestration={"owner": "hub", "workers_may_orchestrate": False},
        governance={"admission_policy": "standard_or_explicit_custom", "separation_of_duties": "strict"},
        budgets={"policy_ref": "budget_policy@1"},
        limit_policy_ref="organization_limits@1",
    )


class FakeDefinitionCatalog:
    def __init__(self) -> None:
        self.organization = organization_definition()
        self.team_blueprints = {
            key: TeamBlueprintDefinition(
                key=key,
                version=1,
                description=f"{key} test blueprint",
                team_kind={
                    "enterprise_product_delivery_scrum": "delivery",
                    "portfolio_product_coordination": "coordination",
                    "research_and_discovery": "research",
                    "proof_of_concept": "poc",
                    "platform_devops_sre": "shared_service",
                    "architecture_governance": "governance",
                    "quality_security_release": "governance",
                }[key],
                role_slots=[
                    RoleSlotDefinition(
                        slot_id="lead",
                        role_template_ref=f"{key}_lead@1",
                        required=True,
                        min_count=1,
                        default_count=1,
                        max_count=1,
                        assignment_policy={
                            "principal_kinds": ["agent"],
                            "required_capabilities": [],
                            "forbidden_capabilities": ["worker_orchestration"],
                            "write_access_required": False,
                        },
                        separation_of_duties={
                            "enforcement": "none",
                            "independent_from_slot_ids": [],
                        },
                        overlays=[],
                    )
                ],
                artifacts=[{"kind": "result", "required": True, "portable": True}],
                workflow_ref=f"{key}_workflow@1",
                policies=["execution_policy@1"],
                capacity_defaults={"min_agents": 1, "default_agents": 1, "max_agents": 4},
            )
            for key in TEAM_KEYS
        }
        self.reads = 0

    def get_organization_blueprint(self, key, version):
        self.reads += 1
        return self.organization if (key, version) == (self.organization.key, self.organization.version) else None

    def get_team_blueprint(self, key, version):
        self.reads += 1
        return self.team_blueprints.get(key) if version == 1 else None

    def has_role_template(self, key, version):
        self.reads += 1
        return version == 1 and key.endswith("_lead")

    def has_workflow_definition(self, key, version):
        self.reads += 1
        return version == 1 and key.endswith("_workflow")

    def get_workflow_definition(self, key, version):
        if not self.has_workflow_definition(key, version):
            return None
        team_key = key.removesuffix("_workflow")
        return {
            "key": key,
            "version": version,
            "mode": "gated",
            "default_failure_policy": "block",
            "steps": [
                {
                    "step_id": "execute",
                    "title": "Execute delegated work",
                    "task_kind": "coding",
                    "owner_role_ref": f"{team_key}_lead@1",
                    "target_team_selector": {
                        "team_blueprint_ref": f"{team_key}@1",
                        "cardinality": 1,
                        "routing": "single",
                    },
                    "depends_on": [],
                    "inputs": [],
                    "outputs": ["result"],
                    "gate": {
                        "required": False,
                        "acceptance_checks": [],
                        "approval_role_ref": None,
                        "independent_principal_required": False,
                    },
                    "failure_policy": "block",
                }
            ],
        }

    def has_handoff_definition(self, key, version):
        self.reads += 1
        return version == 1 and key in {
            "research_handoff",
            "poc_handoff",
            "architecture_handoff",
            "platform_handoff",
            "release_handoff",
        }

    def get_handoff_definition(self, key, version):
        if not self.has_handoff_definition(key, version):
            return None
        return {
            "key": key,
            "version": version,
            "required_artifact_kinds": ["result"],
            "acceptance_gate_ref": "execution_policy@1",
        }

    def has_policy(self, portable_ref):
        self.reads += 1
        return portable_ref in {"organization_limits@1", "execution_policy@1", "budget_policy@1"}


class FakeLimitProfiles:
    def __init__(self, limits=None) -> None:
        self.limits = limits or organization_limits()

    def resolve_limit_profile(self, **_kwargs):
        return self.limits


class FakeAdmissionPolicy:
    def validate_exception(self, **kwargs):
        if kwargs["exception_ref"] == "test-only-small@1":
            return True, None
        return False, "ORGANIZATION_ADMISSION_EXCEPTION_INVALID"


def organization_compiler(catalog=None):
    definitions = catalog or FakeDefinitionCatalog()
    return OrganizationBlueprintCompiler(
        definitions=definitions,
        limit_profiles=FakeLimitProfiles(),
        admission_policy=FakeAdmissionPolicy(),
    )
