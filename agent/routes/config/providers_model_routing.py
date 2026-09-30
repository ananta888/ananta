"""Model-routing configuration API (``/models/consumers/v1``, ``/models/routing/v1*``).

The handlers are registered on the provider blueprint by
:func:`register_model_routing_routes`, so rules and endpoint names are
unchanged (``config_providers.<handler>``).
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Protocol

from flask import Blueprint, request
from pydantic import ValidationError

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.model_invocation_service import (
    ModelRoutingConfigurationError,
)
from agent.services.model_routing_legacy_migration_service import (
    ModelRoutingLegacyMigrationError,
)
from agent.services.model_routing_observability_service import (
    ModelRoutingDiagnosticsService,
    get_model_routing_usage_projection,
)
from agent.services.model_routing_transfer_service import (
    ModelRoutingConfirmationError,
    ModelRoutingTransferService,
)
from agent.services.model_selection_service import (
    ModelRoutingConflict,
)
from ananta_contracts.model_selection import (
    EffectiveModelRoutingProjection,
    ModelRoutingDryRunCommand,
    ModelRoutingImportCommand,
    ModelRoutingLegacyMigrationApplyCommand,
    ModelRoutingMutationCommand,
)

from .providers_route_support import (
    MODEL_ROUTING_EXPORT_CAPABILITY,
    MODEL_ROUTING_MUTATE_CAPABILITY,
    MODEL_ROUTING_READ_CAPABILITY,
    MODEL_ROUTING_VALIDATE_CAPABILITY,
    _capability_allowed,
    _capability_denied_response,
    _feature_disabled_response,
    _model_catalog_feature_enabled,
    _model_catalog_input_error,
    _model_routing_editor_enabled,
    _query_args_are_valid,
    _routing_editor_disabled_response,
)


class ModelRoutingServices(Protocol):
    """Collaborators of the model-routing handlers (injected at registration)."""

    def consumers(self) -> Any: ...

    def profiles(self) -> Any: ...

    def routing(self) -> Any: ...

    def effective_routing(self) -> Any: ...

    def transfer(self) -> Any: ...

    def templates(self) -> Any: ...

    def legacy_migration(self) -> Any: ...

    def inventory(self) -> Any: ...


def get_model_consumers(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    consumers = services.consumers().all()
    return api_response(
        data={
            "schema": "ananta.model-consumer-registry.v1",
            "consumers": [item.model_dump(mode="json", by_alias=True) for item in consumers],
        }
    )


def get_model_routing_configuration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    value = services.routing().read()
    return api_response(data=value.model_dump(mode="json", by_alias=True))


def get_effective_model_routing_projection(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    if not _query_args_are_valid():
        return _model_catalog_input_error("model_routing_effective_query_invalid")
    try:
        routing = services.routing().read()
        effective = services.effective_routing()
        routes = tuple(
            effective.dry_run(
                ModelRoutingDryRunCommand(
                    consumer_id=consumer.consumer_id,
                )
            )
            for consumer in services.consumers().all()
            if consumer.routable
        )
    except ModelRoutingConfigurationError as exc:
        return api_response(status="error", message=str(exc)[:160], code=503)
    projection = EffectiveModelRoutingProjection(
        configuration_revision=routing.revision,
        routes=routes,
    )
    return api_response(data=projection.model_dump(mode="json", by_alias=True))


def get_model_routing_templates(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    if not _query_args_are_valid():
        return _model_catalog_input_error("model_routing_template_query_invalid")
    revision = services.routing().read().revision
    catalog = services.templates().catalog(configuration_revision=revision)
    return api_response(data=catalog.model_dump(mode="json", by_alias=True))


def preview_legacy_model_routing_migration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    preview = services.legacy_migration().preview()
    return api_response(data=preview.model_dump(mode="json", by_alias=True))


def apply_legacy_model_routing_migration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_MUTATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_MUTATE_CAPABILITY)
    try:
        command = ModelRoutingLegacyMigrationApplyCommand.model_validate(request.get_json(silent=True))
        updated = services.legacy_migration().apply(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_legacy_migration_command_invalid")
    except ModelRoutingLegacyMigrationError as exc:
        code = 409 if str(exc) == "model_routing_revision_conflict" else 400
        return api_response(status="error", message=str(exc), code=code)
    log_audit(
        "model_routing_legacy_migration_applied",
        {
            "previous_revision": command.expected_revision,
            "revision": updated.revision,
            "assignment_count": len(updated.assignments),
        },
    )
    return api_response(data=updated.model_dump(mode="json", by_alias=True))


def get_model_routing_shadow_report(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    report = services.legacy_migration().shadow_report()
    return api_response(data=report.model_dump(mode="json", by_alias=True))


def get_model_routing_release_gate(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    report = services.legacy_migration().release_gate()
    return api_response(data=report.model_dump(mode="json", by_alias=True))


def _model_routing_diagnostics_read_model(services: ModelRoutingServices):
    configuration = services.routing().read()
    catalog = services.inventory().catalog(force_refresh=False)
    consumers = services.consumers().all()
    effective = services.effective_routing()
    routes = tuple(
        effective.dry_run(ModelRoutingDryRunCommand(consumer_id=item.consumer_id))
        for item in consumers
        if item.routable
    )
    return ModelRoutingDiagnosticsService().build(
        configuration=configuration,
        catalog=catalog,
        consumers=consumers,
        effective_routes=routes,
        known_profile_ids=(item.profile_id for item in services.profiles()),
        usage=get_model_routing_usage_projection().read(),
    )


def get_model_routing_diagnostics(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    if not _query_args_are_valid():
        return _model_catalog_input_error("model_routing_diagnostics_query_invalid")
    try:
        diagnostics = _model_routing_diagnostics_read_model(services)
    except ModelRoutingConfigurationError as exc:
        return api_response(status="error", message=str(exc)[:160], code=503)
    return api_response(data=diagnostics.model_dump(mode="json", by_alias=True))


def export_model_routing_diagnostics(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_EXPORT_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_EXPORT_CAPABILITY)
    if not _query_args_are_valid():
        return _model_catalog_input_error("model_routing_diagnostics_query_invalid")
    try:
        diagnostics = _model_routing_diagnostics_read_model(services)
    except ModelRoutingConfigurationError as exc:
        return api_response(status="error", message=str(exc)[:160], code=503)
    log_audit(
        "model_routing_diagnostics_exported",
        {
            "configuration_revision": diagnostics.configuration_revision,
            "catalog_revision": diagnostics.catalog_revision,
            "issue_count": len(diagnostics.issues),
            "contains_secrets": False,
        },
    )
    result = api_response(data=diagnostics.model_dump(mode="json", by_alias=True))
    response = result[0] if isinstance(result, tuple) else result
    response.headers["Content-Disposition"] = 'attachment; filename="ananta-model-routing-diagnostics.json"'
    return result


def dry_run_model_routing_configuration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_READ_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_READ_CAPABILITY)
    try:
        command = ModelRoutingDryRunCommand.model_validate(request.get_json(silent=True))
        if command.configuration is not None:
            assignment_service = services.routing()
            assignment_service.validate(
                ModelRoutingMutationCommand(
                    schema="ananta.model-routing-mutation-command.v1",
                    expected_revision=assignment_service.read().revision,
                    assignments=command.configuration.assignments,
                    fallback_groups=command.configuration.fallback_groups,
                )
            )
        route = services.effective_routing().dry_run(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_dry_run_command_invalid")
    except ValueError as exc:
        return api_response(
            status="error",
            message=str(exc)[:160],
            code=400,
        )
    except ModelRoutingConfigurationError as exc:
        return api_response(
            status="error",
            message=str(exc)[:160],
            code=503,
        )
    return api_response(data=route.model_dump(mode="json", by_alias=True))


def validate_model_routing_configuration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _model_routing_editor_enabled():
        return _routing_editor_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_VALIDATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_VALIDATE_CAPABILITY)
    try:
        command = ModelRoutingMutationCommand.model_validate(request.get_json(silent=True))
        report = services.transfer().validate(command)
    except ValidationError as exc:
        return api_response(
            status="error",
            message="model_routing_configuration_invalid",
            data={"reason_code": str(exc).splitlines()[0][:160]},
            code=400,
        )
    log_audit(
        "model_routing_configuration_validated",
        {
            "expected_revision": command.expected_revision,
            "current_revision": report.current_revision,
            "valid": report.valid,
            "issue_count": len(report.issues),
        },
    )
    from agent import metrics

    for issue in report.issues:
        metrics.MODEL_ROUTING_VALIDATION_ERRORS_TOTAL.labels(severity=issue.severity).inc()
    return api_response(data=report.model_dump(mode="json", by_alias=True))


def export_model_routing_configuration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_EXPORT_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_EXPORT_CAPABILITY)
    bundle = services.transfer().export()
    log_audit(
        "model_routing_configuration_exported",
        {
            "revision": bundle.configuration.revision,
            "assignment_count": len(bundle.configuration.assignments),
            "fallback_group_count": len(bundle.configuration.fallback_groups),
        },
    )
    return api_response(data=bundle.model_dump(mode="json", by_alias=True))


def preview_model_routing_import(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _model_routing_editor_enabled():
        return _routing_editor_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_VALIDATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_VALIDATE_CAPABILITY)
    try:
        command = ModelRoutingImportCommand.model_validate(request.get_json(silent=True))
        preview = services.transfer().preview(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_import_command_invalid")
    log_audit(
        "model_routing_import_previewed",
        {
            "expected_revision": command.expected_revision,
            "source_revision": command.configuration.revision,
            "applicable": preview.applicable,
            "issue_count": len(preview.issues),
        },
    )
    return api_response(data=preview.model_dump(mode="json", by_alias=True))


def apply_model_routing_import(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _model_routing_editor_enabled():
        return _routing_editor_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_MUTATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_MUTATE_CAPABILITY)
    try:
        command = ModelRoutingImportCommand.model_validate(request.get_json(silent=True))
        updated = services.transfer().apply(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_import_command_invalid")
    except ModelRoutingConfirmationError as exc:
        return api_response(status="error", message=str(exc), code=400)
    except ModelRoutingConflict as exc:
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"current_revision": exc.current_revision},
            code=409,
        )
    except ValueError as exc:
        return api_response(
            status="error",
            message="model_routing_configuration_invalid",
            data={"reason_code": str(exc)[:160]},
            code=400,
        )
    log_audit(
        "model_routing_import_applied",
        {
            "previous_revision": command.expected_revision,
            "source_revision": command.configuration.revision,
            "revision": updated.revision,
            "assignment_count": len(updated.assignments),
            "fallback_group_count": len(updated.fallback_groups),
        },
    )
    return api_response(data=updated.model_dump(mode="json", by_alias=True))


def put_model_routing_configuration(services: ModelRoutingServices):
    if not _model_catalog_feature_enabled():
        return _feature_disabled_response()
    if not _model_routing_editor_enabled():
        return _routing_editor_disabled_response()
    if not _capability_allowed(MODEL_ROUTING_MUTATE_CAPABILITY):
        return _capability_denied_response(MODEL_ROUTING_MUTATE_CAPABILITY)
    current = services.routing().read()
    try:
        command = ModelRoutingMutationCommand.model_validate(request.get_json(silent=True))
        updated = services.routing().apply(command)
    except ValidationError:
        return _model_catalog_input_error("model_routing_mutation_command_invalid")
    except ModelRoutingConflict as exc:
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"current_revision": exc.current_revision},
            code=409,
        )
    except ValueError as exc:
        return api_response(
            status="error",
            message="model_routing_configuration_invalid",
            data={"reason_code": str(exc)[:160]},
            code=400,
        )
    diff = ModelRoutingTransferService.diff(current, updated)
    log_audit(
        "model_routing_configuration_updated",
        {
            "previous_revision": command.expected_revision,
            "revision": updated.revision,
            "assignment_count": len(updated.assignments),
            "fallback_group_count": len(updated.fallback_groups),
            "diff": diff.model_dump(mode="json"),
        },
    )
    return api_response(data=updated.model_dump(mode="json", by_alias=True))


_MODEL_ROUTING_ROUTES = (
    ("/models/consumers/v1", ("GET",), get_model_consumers),
    ("/models/routing/v1", ("GET",), get_model_routing_configuration),
    ("/models/routing/v1/effective", ("GET",), get_effective_model_routing_projection),
    ("/models/routing/v1/templates", ("GET",), get_model_routing_templates),
    ("/models/routing/v1/migration/preview", ("GET",), preview_legacy_model_routing_migration),
    ("/models/routing/v1/migration/apply", ("POST",), apply_legacy_model_routing_migration),
    ("/models/routing/v1/shadow", ("GET",), get_model_routing_shadow_report),
    ("/models/routing/v1/release-gate", ("GET",), get_model_routing_release_gate),
    ("/models/routing/v1/diagnostics", ("GET",), get_model_routing_diagnostics),
    ("/models/routing/v1/diagnostics/export", ("GET",), export_model_routing_diagnostics),
    ("/models/routing/v1/dry-run", ("POST",), dry_run_model_routing_configuration),
    ("/models/routing/v1/validate", ("POST",), validate_model_routing_configuration),
    ("/models/routing/v1/export", ("GET",), export_model_routing_configuration),
    ("/models/routing/v1/import/preview", ("POST",), preview_model_routing_import),
    ("/models/routing/v1/import/apply", ("POST",), apply_model_routing_import),
    ("/models/routing/v1", ("PUT",), put_model_routing_configuration),
)


def _bind(view: Callable[..., Any], services: ModelRoutingServices) -> Callable[..., Any]:
    @functools.wraps(view)
    def bound(*args: Any, **kwargs: Any) -> Any:
        return view(services, *args, **kwargs)

    return bound


def register_model_routing_routes(blueprint: Blueprint, *, services: ModelRoutingServices) -> None:
    """Register the model-routing handlers (with ``check_auth``) on ``blueprint``."""

    for rule, methods, view in _MODEL_ROUTING_ROUTES:
        blueprint.route(rule, methods=list(methods), endpoint=view.__name__)(check_auth(_bind(view, services)))
