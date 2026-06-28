from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NullabilityModel:
    state: str
    optional_absence: bool = False
    warnings: tuple[str, ...] = ()


def infer_java_nullability(type_name: str, annotations: list[str] | tuple[str, ...] | None = None) -> NullabilityModel:
    normalized_type = str(type_name or "").strip()
    normalized_annotations = {str(item).lower().strip("@") for item in list(annotations or [])}
    if normalized_type.startswith("Optional<"):
        return NullabilityModel(state="optional_absence", optional_absence=True)
    if {"nonnull", "notnull", "not_null"}.intersection(normalized_annotations):
        return NullabilityModel(state="non_null")
    if {"nullable"}.intersection(normalized_annotations):
        return NullabilityModel(state="nullable")
    if normalized_type in {"int", "long", "double", "float", "boolean", "byte", "short", "char"}:
        return NullabilityModel(state="non_null")
    return NullabilityModel(state="unknown_nullability", warnings=("unknown_nullability",))
