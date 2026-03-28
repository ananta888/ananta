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
        "is_dto": is_dto,
        "is_record_like": is_record_like,
        "role_labels": [
            label for label, enabled in [
                ("lombok", is_lombok),
                ("entity", is_entity),
                ("repository", is_repository),
                ("controller", is_controller),
                ("service", is_service),
                ("dto", is_dto),
                ("record_like", is_record_like),
            ] if enabled
        ],
    }
