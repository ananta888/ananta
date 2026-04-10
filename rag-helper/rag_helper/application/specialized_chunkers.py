from __future__ import annotations

from collections import defaultdict

from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


def build_specialized_chunks(
    index_records: list[dict],
    detail_records: list[dict],
    mode: str,
    embedding_text_mode: str,
) -> tuple[list[dict], list[dict], dict | None]:
    if mode != "basic":
        return [], [], None

    extra_details: list[dict] = []
    extra_relations: list[dict] = []
    stats = defaultdict(int)

    extra_details.extend(_build_spring_xml_chunks(detail_records, extra_relations, stats, embedding_text_mode))
    extra_details.extend(_build_maven_pom_chunks(detail_records, extra_relations, stats, embedding_text_mode))
    extra_details.extend(_build_xsd_schema_chunks(index_records, extra_relations, stats, embedding_text_mode))
    extra_details.extend(_build_adoc_architecture_chunks(index_records, extra_relations, stats, embedding_text_mode))
    extra_details.extend(_build_jpa_entity_chunks(index_records, extra_relations, stats, embedding_text_mode))
    extra_details.extend(_build_type_member_chunks(index_records, extra_relations, stats, embedding_text_mode))

    return extra_details, extra_relations, dict(stats)


def _build_spring_xml_chunks(detail_records, extra_relations, stats, embedding_text_mode):
    chunks = []
    for record in detail_records:
        if record.get("kind") != "xml_node_detail" or record.get("tag") != "bean":
            continue
        attrs = record.get("attributes", {})
        bean_name = attrs.get("id") or attrs.get("name") or attrs.get("class")
        if not bean_name:
            continue
        chunk_id = f"spring_xml_bean_chunk:{safe_id(record.get('file', ''), record.get('path', ''))}"
        chunks.append({
            "kind": "spring_xml_bean_chunk",
            "file": record.get("file"),
            "id": chunk_id,
            "parent_id": f"xml_file:{safe_id(record.get('file', ''))}",
            "bean_name": bean_name,
            "bean_class": attrs.get("class"),
            "scope": attrs.get("scope"),
            "path": record.get("path"),
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                f"Spring XML bean {bean_name} in {record.get('file')}. Class {attrs.get('class') or 'none'}. Scope {attrs.get('scope') or 'default'}.",
                f"Spring bean {bean_name}. Class {attrs.get('class') or 'none'}.",
            ),
        })
        extra_relations.append({
            "kind": "relation",
            "file": record.get("file"),
            "id": f"relation:{safe_id(chunk_id, record.get('id', ''), 'specializes')}",
            "source_id": chunk_id,
            "source_kind": "spring_xml_bean_chunk",
            "source_name": bean_name,
            "relation": "specializes_xml_node",
            "target": record.get("path"),
            "target_resolved": record.get("id"),
            "weight": 1,
            "from": chunk_id,
            "to": record.get("id"),
            "type": "specializes_xml_node",
        })
        stats["spring_xml_bean_chunk_count"] += 1
    return chunks


def _build_maven_pom_chunks(detail_records, extra_relations, stats, embedding_text_mode):
    chunks = []
    by_file = defaultdict(list)
    for record in detail_records:
        if record.get("kind") == "xml_node_detail" and record.get("file", "").endswith("pom.xml"):
            by_file[record["file"]].append(record)

    for file, records in by_file.items():
        values = {record.get("path"): record.get("text", "") for record in records}
        group_id = values.get("/project/groupId") or values.get("/project/parent/groupId")
        artifact_id = values.get("/project/artifactId")
        version = values.get("/project/version") or values.get("/project/parent/version")
        dependency_names = []
        dependency_groups = defaultdict(dict)
        for record in records:
            path = record.get("path", "")
            if "/dependencies/dependency/" not in path:
                continue
            dep_root = path.rsplit("/", 1)[0]
            dependency_groups[dep_root][record.get("tag")] = record.get("text", "")
        for dep_root, dep_values in dependency_groups.items():
            name = ":".join(filter(None, [dep_values.get("groupId"), dep_values.get("artifactId"), dep_values.get("version")]))
            if name:
                dependency_names.append(name)
                chunk_id = f"maven_dependency_chunk:{safe_id(file, dep_root)}"
                chunks.append({
                    "kind": "maven_dependency_chunk",
                    "file": file,
                    "id": chunk_id,
                    "parent_id": f"maven_pom_chunk:{safe_id(file)}",
                    "dependency": name,
                    "scope": dep_values.get("scope"),
                    "embedding_text": build_embedding_text(
                        embedding_text_mode,
                        f"Maven dependency {name} in {file}. Scope {dep_values.get('scope') or 'default'}.",
                        f"Maven dependency {name}.",
                    ),
                })
                stats["maven_dependency_chunk_count"] += 1

        pom_chunk_id = f"maven_pom_chunk:{safe_id(file)}"
        chunks.append({
            "kind": "maven_pom_chunk",
            "file": file,
            "id": pom_chunk_id,
            "parent_id": f"xml_file:{safe_id(file)}",
            "coordinates": ":".join(filter(None, [group_id, artifact_id, version])),
            "dependencies": dependency_names[:50],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                f"Maven POM {file}. Coordinates {group_id or 'none'}:{artifact_id or 'none'}:{version or 'none'}. Dependencies {', '.join(dependency_names[:20]) or 'none'}.",
                f"Maven POM {artifact_id or file}. Dependencies {compact_list(dependency_names, limit=6)}.",
            ),
        })
        extra_relations.append({
            "kind": "relation",
            "file": file,
            "id": f"relation:{safe_id(file, pom_chunk_id, 'specializes_pom')}",
            "source_id": pom_chunk_id,
            "source_kind": "maven_pom_chunk",
            "source_name": artifact_id or file,
            "relation": "specializes_xml_file",
            "target": file,
            "target_resolved": f"xml_file:{safe_id(file)}",
            "weight": 1,
            "from": pom_chunk_id,
            "to": f"xml_file:{safe_id(file)}",
            "type": "specializes_xml_file",
        })
        stats["maven_pom_chunk_count"] += 1
    return chunks


def _build_xsd_schema_chunks(index_records, extra_relations, stats, embedding_text_mode):
    chunks = []
    by_file = defaultdict(lambda: {"complex": [], "simple": [], "root": []})
    for record in index_records:
        kind = record.get("kind")
        file = record.get("file")
        if kind == "xsd_complex_type":
            by_file[file]["complex"].append(record.get("name"))
        elif kind == "xsd_simple_type":
            by_file[file]["simple"].append(record.get("name"))
        elif kind == "xsd_root_element":
            by_file[file]["root"].append(record.get("name"))
    for file, values in by_file.items():
        chunk_id = f"xsd_schema_chunk:{safe_id(file)}"
        chunks.append({
            "kind": "xsd_schema_chunk",
            "file": file,
            "id": chunk_id,
            "parent_id": f"xsd_file:{safe_id(file)}",
            "complex_types": values["complex"][:50],
            "simple_types": values["simple"][:50],
            "root_elements": values["root"][:50],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                f"XSD schema {file}. Complex types {', '.join(values['complex'][:20]) or 'none'}. Root elements {', '.join(values['root'][:20]) or 'none'}.",
                f"XSD schema {file}. Complex {compact_list(values['complex'], limit=6)}. Roots {compact_list(values['root'], limit=6)}.",
            ),
        })
        stats["xsd_schema_chunk_count"] += 1
    return chunks


def _build_adoc_architecture_chunks(index_records, extra_relations, stats, embedding_text_mode):
    chunks = []
    for record in index_records:
        if record.get("kind") != "adoc_section":
            continue
        title = (record.get("title") or "").lower()
        if not any(token in title for token in ("architecture", "overview", "design")):
            continue
        chunk_id = f"adoc_architecture_chunk:{safe_id(record.get('id', ''))}"
        chunks.append({
            "kind": "adoc_architecture_chunk",
            "file": record.get("file"),
            "id": chunk_id,
            "parent_id": record.get("id"),
            "title": record.get("title"),
            "section_path": record.get("section_path"),
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                f"Architecture AsciiDoc section {record.get('title')} in {record.get('file')}. Path {' > '.join(record.get('section_path', [])) or 'none'}.",
                f"Architecture section {record.get('title')}.",
            ),
        })
        stats["adoc_architecture_chunk_count"] += 1
    return chunks


def _build_jpa_entity_chunks(index_records, extra_relations, stats, embedding_text_mode):
    chunks = []
    for record in index_records:
        if record.get("kind") != "java_type":
            continue
        role_labels = set(record.get("role_labels", []))
        annotations = set(record.get("annotations", []))
        if "entity" not in role_labels and "@Entity" not in annotations:
            continue
        fields = record.get("fields", [])
        field_names = [field.get("name") for field in fields if field.get("name")]
        association_fields = [
            field.get("name")
            for field in fields
            if field.get("name")
            if any(annotation.startswith("@OneTo") or annotation.startswith("@ManyTo") for annotation in field.get("annotations", []))
        ]
        chunk_id = f"jpa_entity_chunk:{safe_id(record.get('id', ''))}"
        chunks.append({
            "kind": "jpa_entity_chunk",
            "file": record.get("file"),
            "id": chunk_id,
            "parent_id": record.get("id"),
            "entity_name": record.get("name"),
            "field_names": field_names[:50],
            "association_fields": association_fields[:20],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                f"JPA entity {record.get('name')} in {record.get('file')}. Fields {', '.join(field_names[:20]) or 'none'}. Associations {', '.join(association_fields[:20]) or 'none'}.",
                f"JPA entity {record.get('name')}. Fields {compact_list(field_names, limit=6)}.",
            ),
        })
        stats["jpa_entity_chunk_count"] += 1
    return chunks


def _chunked(values, size):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _build_type_member_chunks(index_records, extra_relations, stats, embedding_text_mode):
    chunks = []
    for record in index_records:
        record_kind = record.get("kind")
        if record_kind not in {"java_type", "cs_type"}:
            continue
        type_id = record.get("id")
        type_name = record.get("name")
        if not type_id or not type_name:
            continue

        members = []
        for field in record.get("fields", []):
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            members.append(
                {
                    "kind": "field",
                    "name": name,
                    "type": str(field.get("type") or "").strip(),
                    "annotations": [str(item).strip() for item in list(field.get("annotations") or []) if str(item).strip()],
                }
            )
        for method in record.get("methods", []):
            name = str(method.get("name") or "").strip()
            if not name:
                continue
            members.append(
                {
                    "kind": "method",
                    "name": name,
                    "return_type": str(method.get("return_type") or "").strip(),
                    "annotations": [str(item).strip() for item in list(method.get("annotations") or []) if str(item).strip()],
                }
            )

        if len(members) < 4:
            continue

        chunk_kind = "java_member_chunk" if record_kind == "java_type" else "cs_member_chunk"
        chunk_size = 4 if len(members) <= 8 else 6
        for index, group in enumerate(_chunked(members, chunk_size), start=1):
            member_names = [item["name"] for item in group]
            annotations = sorted(
                {
                    annotation.lstrip("@")
                    for item in group
                    for annotation in item.get("annotations", [])
                    if annotation
                }
            )
            focus_terms = [*member_names[:10], *annotations[:6]]
            kind_counts: dict[str, int] = defaultdict(int)
            for item in group:
                kind_counts[item["kind"]] += 1
            chunk_id = f"{chunk_kind}:{safe_id(type_id, str(index))}"
            summary = (
                f"{type_name} members chunk {index}: "
                f"{kind_counts.get('method', 0)} methods, {kind_counts.get('field', 0)} fields."
            )
            chunks.append(
                {
                    "kind": chunk_kind,
                    "file": record.get("file"),
                    "id": chunk_id,
                    "parent_id": type_id,
                    "type_name": type_name,
                    "member_names": member_names,
                    "member_kinds": sorted(kind_counts.keys()),
                    "focus_terms": focus_terms[:12],
                    "chunk_granularity": "member_group",
                    "retrieval_focus": "type_member_neighborhood",
                    "summary": summary,
                    "embedding_text": build_embedding_text(
                        embedding_text_mode,
                        f"{record_kind} {type_name} in {record.get('file')}. "
                        f"Focused member group with {kind_counts.get('method', 0)} methods and "
                        f"{kind_counts.get('field', 0)} fields. "
                        f"Members {', '.join(member_names[:12])}. "
                        f"Focus terms {', '.join(focus_terms[:12]) or 'none'}.",
                        f"{type_name} members {compact_list(member_names, limit=8)}.",
                    ),
                }
            )
            extra_relations.append(
                {
                    "kind": "relation",
                    "file": record.get("file"),
                    "id": f"relation:{safe_id(chunk_id, type_id, 'specializes_type')}",
                    "source_id": chunk_id,
                    "source_kind": chunk_kind,
                    "source_name": type_name,
                    "relation": "specializes_type",
                    "target": type_name,
                    "target_resolved": type_id,
                    "weight": 1,
                    "from": chunk_id,
                    "to": type_id,
                    "type": "specializes_type",
                }
            )
            stats[f"{chunk_kind}_count"] += 1
    return chunks
