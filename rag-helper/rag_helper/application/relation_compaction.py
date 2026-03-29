from __future__ import annotations


RELATION_PRIORITY = {
    "extends": 100,
    "implements": 95,
    "returns": 90,
    "spring_configuration": 90,
    "jpa_entity_role": 90,
    "transactional_boundary": 90,
    "repository_extends_framework": 90,
    "uses_type": 85,
    "field_type_uses": 85,
    "calls_probable_target": 80,
    "duplicate_candidate": 75,
    "contains_child_tag": 30,
    "calls": 25,
    "declares_method": 10,
    "declares_constructor": 10,
    "contains_type": 5,
    "child_of_type": 5,
    "child_of_file": 5,
}


def compact_relation_records(
    relations: list[dict],
    *,
    max_relation_records_per_file: int | None,
) -> tuple[list[dict], dict[str, int]]:
    if not relations:
        return relations, {}

    deduped = _deduplicate_relations(relations)
    stats: dict[str, int] = {}
    if len(deduped) != len(relations):
        stats["deduplicated_relation_count"] = len(relations) - len(deduped)

    if max_relation_records_per_file is None or len(deduped) <= max_relation_records_per_file:
        return deduped, stats

    kept = _keep_highest_priority_relations(deduped, max_relation_records_per_file)
    stats["pruned_relation_count"] = len(deduped) - len(kept)
    stats["original_relation_count"] = len(relations)
    stats["kept_relation_count"] = len(kept)
    return kept, stats


def compact_relation_records_by_file(
    relations: list[dict],
    *,
    max_relation_records_per_file: int | None,
) -> tuple[list[dict], dict[str, dict[str, int]]]:
    if max_relation_records_per_file is None:
        return relations, {}

    by_file: dict[str, list[dict]] = {}
    file_order: list[str] = []
    for relation in relations:
        file_key = str(relation.get("file") or "<unknown>")
        if file_key not in by_file:
            by_file[file_key] = []
            file_order.append(file_key)
        by_file[file_key].append(relation)

    merged: list[dict] = []
    stats: dict[str, dict[str, int]] = {}
    for file_key in file_order:
        compacted, file_stats = compact_relation_records(
            by_file[file_key],
            max_relation_records_per_file=max_relation_records_per_file,
        )
        merged.extend(compacted)
        if file_stats:
            stats[file_key] = file_stats
    return merged, stats


def _deduplicate_relations(relations: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for relation in relations:
        key = (
            relation.get("source_id") or relation.get("from"),
            _relation_type(relation),
            relation.get("target_resolved") or relation.get("to") or relation.get("target"),
            relation.get("target"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(relation)
    return deduped


def _keep_highest_priority_relations(relations: list[dict], limit: int) -> list[dict]:
    indexed_relations = list(enumerate(relations))
    indexed_relations.sort(
        key=lambda item: (
            -RELATION_PRIORITY.get(_relation_type(item[1]), 50),
            item[0],
        )
    )
    keep_indexes = sorted(index for index, _ in indexed_relations[:limit])
    return [relations[index] for index in keep_indexes]


def _relation_type(relation: dict) -> str:
    value = relation.get("relation") or relation.get("type")
    return str(value or "")
