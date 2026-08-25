from agent.services.model_profile_loader import ModelProfile
from agent.services.model_routing_template_service import ModelRoutingTemplateService
from agent.services.model_selection_service import ModelConsumerRegistry


def _profile(
    profile_id: str,
    *,
    role: str = "any",
    cloud: bool = False,
    provider: str = "lmstudio",
    tools: bool = True,
    json: bool = True,
) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id=provider,
        model=profile_id,
        model_role=role,
        local=not cloud,
        cloud=cloud,
        cloud_allowed=cloud,
        block_secret_context=cloud,
        supports_tools=tools,
        supports_json=json,
        retry_budget=1,
    )


def test_templates_are_secret_free_drafts_at_the_current_revision():
    service = ModelRoutingTemplateService(
        consumers=ModelConsumerRegistry.defaults(),
        profiles=(
            _profile("local-any"),
            _profile("local-code", role="coder"),
            _profile("local-embed", role="embedder"),
            _profile("cloud-any", cloud=True, provider="openrouter"),
            _profile("cli-code", role="coder", provider="codex"),
        ),
    )

    catalog = service.catalog(configuration_revision=7)

    assert [item.template_id for item in catalog.templates] == [
        "local-only", "local-first-cloud-fallback", "cloud-only", "cli-first",
    ]
    assert all(item.configuration.revision == 7 for item in catalog.templates)
    serialized = catalog.model_dump_json(by_alias=True)
    assert "base_url" not in serialized
    assert "api_key" not in serialized


def test_local_only_never_references_cloud_and_local_first_orders_cloud_last():
    service = ModelRoutingTemplateService(
        consumers=ModelConsumerRegistry((
            ModelConsumerRegistry.defaults().require("task.coding"),
        )),
        profiles=(
            _profile("local-code", role="coder"),
            _profile("local-code-secondary", role="coder"),
            _profile("cloud-code", role="coder", cloud=True, provider="openrouter"),
        ),
    )
    templates = {
        item.template_id: item for item in service.catalog(configuration_revision=2).templates
    }

    local_ids = {
        candidate.profile_id
        for group in templates["local-only"].configuration.fallback_groups
        for candidate in group.candidates
    }
    assert "cloud-code" not in local_ids
    local_first = templates["local-first-cloud-fallback"].configuration
    candidates = local_first.fallback_groups[0].candidates
    assert [item.profile_id for item in candidates][-1] == "cloud-code"
    assert candidates[-1].cloud_allowed is True


def test_unavailable_template_is_explicit_instead_of_inventing_cli_profiles():
    service = ModelRoutingTemplateService(
        consumers=ModelConsumerRegistry((
            ModelConsumerRegistry.defaults().require("task.coding"),
        )),
        profiles=(_profile("local-code", role="coder"),),
    )

    cli = service.catalog(configuration_revision=0).templates[-1]

    assert cli.applicable is False
    assert cli.configuration.assignments == ()
    assert {issue.reason_code for issue in cli.issues} == {
        "model_routing_template_consumer_unresolved",
        "model_routing_template_no_compatible_profiles",
    }


def test_templates_ignore_consumers_owned_by_specialized_runtime_domains():
    service = ModelRoutingTemplateService(
        consumers=ModelConsumerRegistry.defaults(),
        profiles=(_profile("local-any"),),
    )

    template = service.catalog(configuration_revision=0).templates[0]
    assigned_consumers = {
        assignment.consumer_id for assignment in template.configuration.assignments
    }
    issue_references = {issue.reference for issue in template.issues}

    assert "knowledge.embedding" not in assigned_consumers
    assert "knowledge.embedding" not in issue_references
    assert "tiny_router.action" not in assigned_consumers
