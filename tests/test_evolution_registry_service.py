import pytest

from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionProviderNotFound,
    EvolutionProviderRegistry,
    EvolutionResult,
    EvolutionTriggerType,
    NoEvolutionProviderAvailable,
    UnsupportedEvolutionOperation,
    ValidationResult,
)
from agent.services.evolution_service import EvolutionService


class SimpleEngine(EvolutionEngine):
    def __init__(self, provider_name: str, capabilities=None):
        self._provider_name = provider_name
        self._capabilities = capabilities or [EvolutionCapability.ANALYZE]

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def capabilities(self):
        return self._capabilities

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)


class ValidatingEngine(SimpleEngine):
    def __init__(self, provider_name: str):
        super().__init__(provider_name, [EvolutionCapability.ANALYZE, EvolutionCapability.VALIDATE])

    def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
        return ValidationResult(proposal_id=proposal.proposal_id, status="passed", valid=True)


def test_registry_registers_multiple_providers_and_reports_default():
    registry = EvolutionProviderRegistry()

    registry.register(SimpleEngine("alpha"))
    registry.register(ValidatingEngine("beta"), default=True)

    assert registry.default_provider_name == "beta"
    assert registry.resolve().provider_name == "beta"
    assert registry.resolve("alpha").provider_name == "alpha"
    descriptors = registry.list_descriptors()
    assert [item["provider_name"] for item in descriptors] == ["alpha", "beta"]
    assert [item["default"] for item in descriptors] == [False, True]


def test_registry_resolves_provider_from_config_and_fails_clearly():
    registry = EvolutionProviderRegistry()
    registry.register(SimpleEngine("alpha"))

    assert registry.resolve(config={"default_provider": "alpha"}).provider_name == "alpha"
    nested_config = {"default_provider": "ollama", "evolution": {"default_provider": "alpha"}}
    assert registry.resolve(config=nested_config).provider_name == "alpha"

    with pytest.raises(EvolutionProviderNotFound) as exc:
        registry.resolve("missing")

    assert str(exc.value) == "evolution_provider_not_found:missing"


def test_registry_requires_analyze_capability():
    registry = EvolutionProviderRegistry()

    with pytest.raises(ValueError, match="evolution_provider_must_support_analyze"):
        registry.register(SimpleEngine("validate-only", [EvolutionCapability.VALIDATE]))


def test_empty_registry_fails_closed():
    registry = EvolutionProviderRegistry()

    with pytest.raises(NoEvolutionProviderAvailable):
        registry.resolve()


def test_evolution_service_selects_provider_and_emits_audit_metadata():
    registry = EvolutionProviderRegistry()
    registry.register(SimpleEngine("alpha"))
    audits: list[tuple[str, dict]] = []
    service = EvolutionService(registry=registry, audit_fn=lambda action, details: audits.append((action, details)))

    result = service.analyze(EvolutionContext(objective="Improve proposal flow", task_id="T-2"))

    assert result.provider_name == "alpha"
    assert result.summary == "Improve proposal flow"
    assert [item[0] for item in audits] == ["evolution_analysis_requested", "evolution_analysis_completed"]
    assert audits[1][1]["provider_name"] == "alpha"
    assert audits[1][1]["task_id"] == "T-2"


def test_evolution_service_keeps_unsupported_validation_fail_closed():
    registry = EvolutionProviderRegistry()
    registry.register(SimpleEngine("alpha"))
    audits: list[tuple[str, dict]] = []
    service = EvolutionService(registry=registry, audit_fn=lambda action, details: audits.append((action, details)))

    with pytest.raises(UnsupportedEvolutionOperation):
        service.validate(
            EvolutionContext(objective="Validate"),
            EvolutionProposal(title="Proposal", description="Description"),
        )

    assert [item[0] for item in audits] == ["evolution_validation_requested", "evolution_validation_failed"]


def test_evolution_service_provider_analyze_only_policy_blocks_validate_before_provider_call():
    class CountingValidatingEngine(ValidatingEngine):
        def __init__(self, provider_name: str):
            super().__init__(provider_name)
            self.validate_calls = 0

        def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
            self.validate_calls += 1
            return super().validate(context, proposal)

    engine = CountingValidatingEngine("external")
    registry = EvolutionProviderRegistry()
    registry.register(engine, default=True)
    service = EvolutionService(registry=registry, audit_fn=lambda *_args: None)

    with pytest.raises(PermissionError, match="evolution_provider_analyze_only"):
        service.validate(
            EvolutionContext(objective="Validate"),
            EvolutionProposal(title="Proposal", description="Description"),
            config={"evolution": {"provider_overrides": {"external": {"force_analyze_only": True}}}},
        )

    assert engine.validate_calls == 0


def test_evolution_service_apply_policy_fails_closed():
    registry = EvolutionProviderRegistry()
    registry.register(SimpleEngine("alpha"))
    service = EvolutionService(registry=registry, audit_fn=lambda *_args: None)

    with pytest.raises(PermissionError, match="evolution_apply_disabled"):
        service.apply(
            EvolutionContext(objective="Apply"),
            EvolutionProposal(title="Proposal", description="Description"),
            config={"evolution": {"apply_allowed": False}},
        )


def test_evolution_service_auto_trigger_decision_is_policy_gated():
    service = EvolutionService(registry=EvolutionProviderRegistry(), audit_fn=lambda *_args: None)

    disabled = service.evaluate_auto_trigger(
        {"id": "T-1", "status": "failed"},
        config={"evolution": {"auto_triggers_enabled": False}},
    )
    allowed = service.evaluate_auto_trigger(
        {"id": "T-1", "status": "failed", "verification_status": {"status": "failed"}},
        config={"evolution": {"auto_triggers_enabled": True}},
    )

    assert disabled.allowed is False
    assert disabled.reasons == ["auto_triggers_disabled"]
    assert allowed.allowed is True
    assert allowed.trigger.trigger_type == EvolutionTriggerType.VERIFICATION_FAILURE
