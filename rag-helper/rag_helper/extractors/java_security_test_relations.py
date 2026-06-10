"""Test-, Controller- und Policy-Kanten fuer die Architecture Query Engine.

CCAQE-014: test_targets_type, test_uses_controller, test_calls_endpoint,
controller_endpoint_declares, mock_injects_dependency.
CCAQE-015: permission_checks_field, role_allows_operation,
interceptor_guards_method.

Alle Kanten aus String-/Annotation-Heuristiken tragen reduzierte Confidence und
ein heuristic-Flag; nur direkte Annotation-Referenzen (X.class, Mapping-Pfade)
gelten als harte Evidence.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from rag_helper.extractors.java_ast_helpers import make_relation

_CLASS_REF_PATTERN = re.compile(r"(\w+)\.class")
_QUOTED_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")
_FIELD_OPERATION_PATTERN = re.compile(r"['\"](\w+)\.(read|create|update|delete)['\"]")
_PARAM_REF_PATTERN = re.compile(r"#(\w+)")
_ROLE_PATTERN = re.compile(r"has(?:Any)?(?:Role|Authority)\s*\(\s*['\"]([\w_]+)['\"]")
_REQUEST_METHOD_PATTERN = re.compile(r"RequestMethod\.(\w+)")
_MOCKMVC_CALL_PATTERN = re.compile(
    r"perform\s*\(\s*(?:MockMvcRequestBuilders\s*\.\s*)?(get|post|put|delete|patch)\s*\(\s*\"([^\"]+)\""
)

_TEST_TYPE_ANNOTATIONS = ("@WebMvcTest", "@SpringBootTest", "@DataJpaTest", "@WebFluxTest")
_TEST_TARGET_ANNOTATIONS = ("@WebMvcTest", "@SpringBootTest", "@ContextConfiguration", "@Import")
_MOCK_FIELD_ANNOTATIONS = ("@MockBean", "@MockitoBean", "@Mock", "@SpyBean")
_MAPPING_ANNOTATIONS = {
    "@GetMapping": "GET",
    "@PostMapping": "POST",
    "@PutMapping": "PUT",
    "@DeleteMapping": "DELETE",
    "@PatchMapping": "PATCH",
    "@RequestMapping": None,
}
_SECURITY_ANNOTATIONS = ("@PreAuthorize", "@PostAuthorize", "@Secured", "@RolesAllowed")
_CUSTOM_GUARD_SUFFIXES = ("Guard", "Permission", "Secured", "Auth", "Access", "Check")
_READ_PREFIXES = ("get", "find", "read", "list", "fetch", "load", "query", "search")
_CREATE_PREFIXES = ("create", "add", "save", "insert", "register", "post")
_UPDATE_PREFIXES = ("update", "set", "change", "modify", "patch", "edit")
_DELETE_PREFIXES = ("delete", "remove", "drop")

ResolveFn = Callable[[str], str | None]


def is_test_type(*, type_name: str, annotations: list[str], rel_path: str) -> bool:
    if type_name.endswith(("Test", "Tests", "IT")):
        return True
    if any(annotation.startswith(_TEST_TYPE_ANNOTATIONS) for annotation in annotations):
        return True
    lowered = rel_path.replace("\\", "/").lower()
    return "/test/" in lowered or "/tests/" in lowered or lowered.startswith(("test/", "tests/"))


def infer_operation_from_method_name(method_name: str) -> str | None:
    lowered = str(method_name or "").strip()
    lowered_lc = lowered.lower()
    for prefixes, operation in (
        (_READ_PREFIXES, "read"),
        (_CREATE_PREFIXES, "create"),
        (_UPDATE_PREFIXES, "update"),
        (_DELETE_PREFIXES, "delete"),
    ):
        if lowered_lc.startswith(prefixes):
            return operation
    return None


def build_test_type_relations(
    *,
    rel_path: str,
    type_id: str,
    type_name: str,
    annotations: list[str],
    fields: list[dict[str, Any]],
    resolve: ResolveFn,
) -> list[dict[str, Any]]:
    """CCAQE-014: type-level test edges (test_targets_type, test_uses_controller,
    mock_injects_dependency)."""
    relations: list[dict[str, Any]] = []
    if not is_test_type(type_name=type_name, annotations=annotations, rel_path=rel_path):
        return relations

    for annotation in annotations:
        if not annotation.startswith(_TEST_TARGET_ANNOTATIONS):
            continue
        for class_ref in _CLASS_REF_PATTERN.findall(annotation):
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation="test_targets_type",
                target=class_ref,
                target_resolved=resolve(class_ref) or class_ref,
                extra={"confidence": 0.95},
            ))

    for field in fields:
        field_annotations = list(field.get("annotations") or [])
        resolved_types = list(field.get("resolved_types") or [])
        field_type = str(field.get("type") or "")
        primary_target = resolved_types[0] if resolved_types else (field_type or None)
        if primary_target and field_type.endswith("Controller"):
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation="test_uses_controller",
                target=field_type,
                target_resolved=primary_target,
                extra={"confidence": 0.85},
            ))
        if primary_target and any(annotation.startswith(_MOCK_FIELD_ANNOTATIONS) for annotation in field_annotations):
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation="mock_injects_dependency",
                target=field_type,
                target_resolved=primary_target,
                extra={"confidence": 0.9},
            ))
    return relations


def build_endpoint_relations(
    *,
    rel_path: str,
    type_id: str,
    type_name: str,
    method_record: dict[str, Any],
) -> list[dict[str, Any]]:
    """CCAQE-014: controller_endpoint_declares from Spring mapping annotations."""
    relations: list[dict[str, Any]] = []
    for annotation in list(method_record.get("annotations") or []):
        for mapping_prefix, http_method in _MAPPING_ANNOTATIONS.items():
            if not annotation.startswith(mapping_prefix):
                continue
            quoted = _QUOTED_PATTERN.search(annotation)
            endpoint_path = quoted.group(1) if quoted else None
            method_match = _REQUEST_METHOD_PATTERN.search(annotation)
            resolved_http_method = http_method or (method_match.group(1).upper() if method_match else None)
            extra: dict[str, Any] = {"confidence": 0.95}
            if endpoint_path:
                extra["endpoint_path"] = endpoint_path
            if resolved_http_method:
                extra["http_method"] = resolved_http_method
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation="controller_endpoint_declares",
                target=str(method_record.get("name") or ""),
                target_resolved=str(method_record.get("id") or ""),
                extra=extra,
            ))
            break
    return relations


def build_test_endpoint_call_relations(
    *,
    rel_path: str,
    method_id: str,
    source_name: str,
    body_text: str,
) -> list[dict[str, Any]]:
    """CCAQE-014: MockMvc perform(get("/x")) -> test_calls_endpoint.

    String matching is heuristic by nature, so confidence is reduced.
    """
    relations: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()
    for match in _MOCKMVC_CALL_PATTERN.finditer(str(body_text or "")):
        http_method = match.group(1).upper()
        endpoint_path = match.group(2)
        key = (http_method, endpoint_path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        relations.append(make_relation(
            file=rel_path,
            source_id=method_id,
            source_kind="java_method",
            source_name=source_name,
            relation="test_calls_endpoint",
            target=endpoint_path,
            target_resolved=None,
            extra={
                "confidence": 0.6,
                "heuristic": "mockmvc_string_match",
                "endpoint_path": endpoint_path,
                "http_method": http_method,
            },
        ))
    return relations


def build_policy_relations(
    *,
    rel_path: str,
    type_name: str,
    method_record: dict[str, Any],
    parameter_bindings: dict[str, str],
    resolve: ResolveFn,
) -> list[dict[str, Any]]:
    """CCAQE-015: security annotations -> permission_checks_field /
    role_allows_operation / interceptor_guards_method.

    Only annotation-level evidence is emitted; nothing is guessed from names
    alone. Operations come from quoted '<field>.<op>' tokens or, as fallback,
    from the method name prefix.
    """
    relations: list[dict[str, Any]] = []
    method_id = str(method_record.get("id") or "")
    method_name = str(method_record.get("name") or "")
    source_name = f"{type_name}.{method_name}"
    fallback_operation = infer_operation_from_method_name(method_name)

    for annotation in list(method_record.get("annotations") or []):
        if annotation.startswith(_SECURITY_ANNOTATIONS):
            field_hits = _FIELD_OPERATION_PATTERN.findall(annotation)
            param_refs = _PARAM_REF_PATTERN.findall(annotation)
            param_target: str | None = None
            for param_name in param_refs:
                bound_type = parameter_bindings.get(param_name)
                if bound_type:
                    param_target = resolve(bound_type) or bound_type
                    break
            for field_name, operation in field_hits:
                relations.append(make_relation(
                    file=rel_path,
                    source_id=method_id,
                    source_kind="java_method",
                    source_name=source_name,
                    relation="permission_checks_field",
                    target=field_name,
                    target_resolved=param_target,
                    extra={
                        "confidence": 0.85,
                        "field": field_name,
                        "operation": operation,
                        "source_file": rel_path,
                    },
                ))
            for role_name in _ROLE_PATTERN.findall(annotation):
                relations.append(make_relation(
                    file=rel_path,
                    source_id=method_id,
                    source_kind="java_method",
                    source_name=source_name,
                    relation="role_allows_operation",
                    target=role_name,
                    target_resolved=None,
                    extra={
                        "confidence": 0.9,
                        "operation": fallback_operation,
                        "source_file": rel_path,
                    },
                ))
            if annotation.startswith(("@Secured", "@RolesAllowed")):
                for quoted_role in _QUOTED_PATTERN.findall(annotation):
                    relations.append(make_relation(
                        file=rel_path,
                        source_id=method_id,
                        source_kind="java_method",
                        source_name=source_name,
                        relation="role_allows_operation",
                        target=quoted_role,
                        target_resolved=None,
                        extra={
                            "confidence": 0.9,
                            "operation": fallback_operation,
                            "source_file": rel_path,
                        },
                    ))
            continue

        annotation_name = annotation.lstrip("@").split("(", 1)[0].strip()
        if annotation_name.endswith(_CUSTOM_GUARD_SUFFIXES) and not annotation.startswith(_SECURITY_ANNOTATIONS):
            guard_type = resolve(annotation_name) or annotation_name
            relations.append(make_relation(
                file=rel_path,
                source_id=guard_type,
                source_kind="java_type",
                source_name=annotation_name,
                relation="interceptor_guards_method",
                target=method_name,
                target_resolved=method_id,
                extra={
                    "confidence": 0.7,
                    "heuristic": "annotation_guard",
                    "source_file": rel_path,
                },
            ))
    return relations
