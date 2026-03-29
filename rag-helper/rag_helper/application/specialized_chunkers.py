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
