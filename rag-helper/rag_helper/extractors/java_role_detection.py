from __future__ import annotations

from typing import Any


def detect_type_roles(
    type_name: str,
    type_kind: str,
    annotations: list[str],
    imports: list[str],
    fields: list[dict[str, Any]],
    methods: list[dict[str, Any]],
) -> dict[str, Any]:
    import_text = " ".join(imports)
    lowered_name = type_name.lower()

    field_count = len(fields)
    method_count = len(methods)

    is_lombok = any(
        x.startswith("@Data")
        or x.startswith("@Getter")
        or x.startswith("@Setter")
        or x.startswith("@Builder")
        or x.startswith("@Value")
        or x.startswith("@NoArgsConstructor")
        or x.startswith("@AllArgsConstructor")
        or x.startswith("@RequiredArgsConstructor")
        for x in annotations
    ) or "lombok." in import_text

    is_entity = any(
        x.startswith("@Entity")
        or x.startswith("@Embeddable")
        or x.startswith("@MappedSuperclass")
        or x.startswith("@Table")
        for x in annotations
    )

    is_repository = (
        type_name.endswith("Repository")
        or any(x.startswith("@Repository") for x in annotations)
    )

    is_controller = (
        type_name.endswith("Controller")
        or any(x.startswith("@Controller") or x.startswith("@RestController") for x in annotations)
    )

    is_service = (
        type_name.endswith("Service")
        or any(x.startswith("@Service") for x in annotations)
    )

    is_configuration = (
        type_name.endswith("Config")
        or type_name.endswith("Configuration")
        or any(x.startswith("@Configuration") for x in annotations)
    )

    is_mapper = (
        type_name.endswith("Mapper")
        or any(x.startswith("@Mapper") for x in annotations)
        or "mybatis" in import_text.lower()
    )

    is_adapter = (
        type_name.endswith("Adapter")
        or lowered_name.startswith("adapter")
    )

    is_client = (
        type_name.endswith("Client")
        or any(
            x.startswith("@FeignClient")
            or x.startswith("@HttpExchange")
            or x.startswith("@RestClient")
            for x in annotations
        )
    )

    is_facade = type_name.endswith("Facade")

    is_util = (
        type_name.endswith("Util")
        or type_name.endswith("Utils")
        or (
            field_count == 0
            and method_count > 0
            and all("static" in method.get("modifiers", []) for method in methods)
        )
    )

    is_exception = (
        type_name.endswith("Exception")
        or type_name.endswith("Error")
    )

    is_enum_model = (
        type_kind == "enum"
        or type_name.endswith("Type")
        or type_name.endswith("Status")
    )

    trivial_methods = [m for m in methods if m.get("is_trivial")]
    is_dto = (
        type_name.endswith("Dto")
        or type_name.endswith("DTO")
        or type_name.endswith("Request")
        or type_name.endswith("Response")
        or (
            not is_entity
            and not is_repository
            and not is_controller
            and not is_service
            and field_count > 0
            and method_count > 0
            and len(trivial_methods) >= max(1, int(method_count * 0.5))
        )
    )

    is_record_like = (
        type_kind == "record"
        or (
            field_count > 0
            and len(trivial_methods) >= max(1, int(method_count * 0.7))
            and not is_service
            and not is_controller
            and not is_repository
        )
    )

    return {
        "is_lombok": is_lombok,
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
                ("lombok", is_lombok),
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
