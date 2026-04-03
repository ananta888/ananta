from __future__ import annotations

from typing import Any


def detect_type_roles(
    type_name: str,
    type_kind: str,
    attributes: list[str],
    usings: list[str],
    fields: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    properties: list[dict[str, Any]],
) -> dict[str, Any]:
    using_text = " ".join(usings).lower()
    attribute_text = " ".join(attributes)
    lowered_name = type_name.lower()

    field_count = len(fields)
    method_count = len(methods)
    property_count = len(properties)
    trivial_members = [item for item in [*methods, *properties] if item.get("is_trivial")]

    is_entity = any(
        token in attribute_text
        for token in ("Table", "Key", "Owned", "ComplexType")
    ) or type_name.endswith("Entity")
    is_repository = (
        type_name.endswith("Repository")
        or lowered_name.endswith("dbcontext")
        or any("DbSet" in str(field.get("type") or "") for field in fields)
    )
    is_controller = (
        type_name.endswith("Controller")
        or "[ApiController" in attribute_text
        or "[Controller" in attribute_text
    )
    is_service = type_name.endswith("Service") or type_name.startswith("I") and type_name.endswith("Service")
    is_configuration = (
        type_name.endswith("Options")
        or type_name.endswith("Configuration")
        or type_name in {"Program", "Startup"}
    )
    is_mapper = type_name.endswith("Mapper") or type_name.endswith("Profile")
    is_adapter = type_name.endswith("Adapter")
    is_client = type_name.endswith("Client") or "httpclient" in using_text
    is_facade = type_name.endswith("Facade")
    is_util = (
        type_name.endswith("Util")
        or type_name.endswith("Utils")
        or (
            field_count == 0
            and property_count == 0
            and method_count > 0
            and all("static" in member.get("modifiers", []) for member in methods)
        )
    )
    is_exception = type_name.endswith("Exception")
    is_enum_model = type_kind == "enum" or type_name.endswith("Status") or type_name.endswith("Type")
    is_dto = (
        type_name.endswith("Dto")
        or type_name.endswith("DTO")
        or type_name.endswith("Request")
        or type_name.endswith("Response")
        or type_name.endswith("Model")
        or (
            not is_entity
            and not is_repository
            and not is_controller
            and not is_service
            and property_count > 0
            and len(trivial_members) >= max(1, int((method_count + property_count) * 0.5))
        )
    )
    is_record_like = (
        type_kind == "record"
        or (
            property_count > 0
            and len(trivial_members) >= max(1, int((method_count + property_count) * 0.7))
            and not is_controller
            and not is_service
            and not is_repository
        )
    )

    return {
        "is_entity": is_entity,
        "is_repository": is_repository,
        "is_controller": is_controller,
        "is_service": is_service,
        "is_configuration": is_configuration,
        "is_mapper": is_mapper,
        "is_adapter": is_adapter,
        "is_client": is_client,
        "is_facade": is_facade,
        "is_util": is_util,
        "is_exception": is_exception,
        "is_enum_model": is_enum_model,
        "is_dto": is_dto,
        "is_record_like": is_record_like,
        "role_labels": [
            label for label, enabled in [
                ("entity", is_entity),
                ("repository", is_repository),
                ("controller", is_controller),
                ("service", is_service),
                ("config", is_configuration),
                ("mapper", is_mapper),
                ("adapter", is_adapter),
                ("client", is_client),
                ("facade", is_facade),
                ("util", is_util),
                ("exception", is_exception),
                ("enum_model", is_enum_model),
                ("dto", is_dto),
                ("record_like", is_record_like),
            ] if enabled
        ],
    }
