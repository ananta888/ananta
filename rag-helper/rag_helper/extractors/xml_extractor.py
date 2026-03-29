from __future__ import annotations

from collections import defaultdict
from typing import Any

from lxml import etree
from rag_helper.extractors.base import FileSkipped
from rag_helper.utils.embedding_text import build_embedding_text, compact_list, compact_text
from rag_helper.utils.ids import safe_id


class XmlExtractor:
    def __init__(
        self,
        include_xml_node_details: bool = True,
        max_xml_nodes: int | None = None,
        xml_mode: str = "all",
        relation_mode: str = "per-node",
        repetitive_child_threshold: int = 25,
        embedding_text_mode: str = "verbose",
    ) -> None:
        self.include_xml_node_details = include_xml_node_details
        self.max_xml_nodes = max_xml_nodes
        self.xml_mode = xml_mode
        self.relation_mode = relation_mode
        self.repetitive_child_threshold = repetitive_child_threshold
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        root = etree.fromstring(text.encode("utf-8", errors="ignore"), parser=parser)
        node_count = sum(1 for elem in root.iter() if isinstance(elem.tag, str))
        if self.max_xml_nodes is not None and node_count > self.max_xml_nodes:
            raise ValueError(
                f"max_xml_nodes_exceeded: {node_count} > {self.max_xml_nodes}"
            )

        xml_kind = self._classify_xml(rel_path, root)
        self._ensure_xml_mode_allowed(xml_kind)

        index_records: list[dict] = []
        detail_records: list[dict] = []
        relation_records: list[dict] = []

        namespaces = dict(root.nsmap) if root.nsmap else {}
        root_tag = self._strip_ns(root.tag)

        index_records.append({
            "kind": "xml_file",
            "file": rel_path,
            "id": f"xml_file:{safe_id(rel_path)}",
            "root": root_tag,
            "namespaces": namespaces,
            "xml_kind": xml_kind,
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                f"XML file {rel_path}. Root tag {root_tag}. "
                f"XML kind {xml_kind}. "
                f"Namespaces: {', '.join([f'{k}={v}' for k, v in namespaces.items()][:10]) or 'none'}."
                ),
                (
                f"XML {rel_path}. Root {root_tag}. Kind {xml_kind}. "
                f"Namespaces {compact_list([f'{k}={v}' for k, v in namespaces.items()], limit=4)}."
                ),
            ),
        })

        idx, det, rel = self._extract_xml(rel_path, root)
        index_records.extend(idx)
        detail_records.extend(det)
        relation_records.extend(rel)

        stats = {
            "kind": "xml",
            "file": rel_path,
            "root": root_tag,
            "xml_kind": xml_kind,
            "node_count": node_count,
            "index_count": len(index_records),
            "detail_count": len(detail_records),
            "relation_count": len(relation_records),
        }
        return index_records, detail_records, relation_records, stats

    def _ensure_xml_mode_allowed(self, xml_kind: str) -> None:
        if self.xml_mode == "all":
            return
        if self.xml_mode == "config-only" and xml_kind != "config":
            raise FileSkipped(
                reason="xml_mode_filtered",
                details={"xml_mode": self.xml_mode, "xml_kind": xml_kind},
            )
        if self.xml_mode == "smart" and xml_kind == "data":
            raise FileSkipped(
                reason="xml_mode_filtered",
                details={"xml_mode": self.xml_mode, "xml_kind": xml_kind},
            )

    def _classify_xml(self, rel_path: str, root) -> str:
        if self._looks_like_config_xml(rel_path, root):
            return "config"
        if self._looks_repetitive_data_xml(root):
            return "data"
        return "generic"

    def _looks_like_config_xml(self, rel_path: str, root) -> bool:
        rel_path_lower = rel_path.lower()
        root_tag = self._strip_ns(root.tag).lower()
        namespace_values = {
            value.lower()
            for value in (root.nsmap or {}).values()
            if isinstance(value, str)
        }

        if any(token in rel_path_lower for token in ("spring", "beans", "context", "mapper", "mybatis")):
            return True
        if root_tag in {"beans", "bean", "mapper", "sqlmap", "configuration"}:
            return True
        if any("springframework.org/schema/beans" in value for value in namespace_values):
            return True
        if any("mybatis.org" in value for value in namespace_values):
            return True
        if root.xpath(".//*[@class or @type or @resource or @factory-bean or @factory-method]"):
            return True
        return False

    def _looks_repetitive_data_xml(self, root) -> bool:
        children = [child for child in root if isinstance(child.tag, str)]
        if len(children) < self.repetitive_child_threshold:
            return False

        tag_counts: dict[str, int] = defaultdict(int)
        text_like_children = 0
        attribute_name_sets: set[tuple[str, ...]] = set()
        for child in children:
            tag = self._strip_ns(child.tag).lower()
            tag_counts[tag] += 1
            if (child.text or "").strip():
                text_like_children += 1
            attribute_name_sets.add(tuple(sorted(child.attrib.keys())))

        dominant_count = max(tag_counts.values(), default=0)
        dominant_ratio = dominant_count / len(children) if children else 0.0

        return (
            dominant_ratio >= 0.7
            and len(attribute_name_sets) <= 3
            and text_like_children <= len(children) // 2
        )

    def _strip_ns(self, tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def _path_of(self, elem) -> str:
        parts = []
        cur = elem
        while cur is not None:
            if isinstance(cur.tag, str):
                parts.append(self._strip_ns(cur.tag))
            cur = cur.getparent()
        return "/" + "/".join(reversed(parts))

    def _make_relation(
        self,
        file: str,
        source_id: str,
        source_kind: str,
        source_name: str,
        relation: str,
        target: str,
        target_resolved: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": "relation",
            "file": file,
            "id": f"relation:{safe_id(file, source_id, relation, target, target_resolved or '')}",
            "source_id": source_id,
            "source_kind": source_kind,
            "source_name": source_name,
            "relation": relation,
            "target": target,
            "target_resolved": target_resolved,
            "weight": 1,
        }

    def _extract_xml(self, rel_path: str, root) -> tuple[list[dict], list[dict], list[dict]]:
        index_records = []
        detail_records = []
        relation_records = []
        tag_first_seen = {}
        tag_attrs = defaultdict(set)
        tag_children = defaultdict(set)
        seen_tag_relations: set[tuple[str, str]] = set()

        for elem in root.iter():
            if not isinstance(elem.tag, str):
                continue
            tag = self._strip_ns(elem.tag)
            path = self._path_of(elem)
            attrs = dict(elem.attrib)
            text = (elem.text or "").strip()
            child_tags = [self._strip_ns(c.tag) for c in elem if isinstance(c.tag, str)]
            node_id = f"xml_node:{safe_id(rel_path, path)}"

            if tag not in tag_first_seen:
                tag_first_seen[tag] = path
            tag_attrs[tag].update(attrs.keys())
            tag_children[tag].update(child_tags)

            for child_tag in child_tags:
                if self.relation_mode == "by-tag":
                    relation_key = (tag, child_tag)
                    if relation_key in seen_tag_relations:
                        continue
                    seen_tag_relations.add(relation_key)
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=f"xml_tag:{safe_id(rel_path, tag)}",
                        source_kind="xml_tag",
                        source_name=tag,
                        relation="contains_child_tag",
                        target=child_tag,
                        target_resolved=f"xml_tag:{safe_id(rel_path, child_tag)}",
                    ))
                    continue
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=node_id,
                    source_kind="xml_node",
                    source_name=path,
                    relation="contains_child_tag",
                    target=child_tag,
                    target_resolved=None,
                ))

            if self.include_xml_node_details:
                detail_records.append({
                    "kind": "xml_node_detail",
                    "file": rel_path,
                    "id": f"xml_node_detail:{safe_id(rel_path, path)}",
                    "tag": tag,
                    "path": path,
                    "attributes": attrs,
                    "text": text[:500],
                    "children": child_tags[:100],
                    "embedding_text": build_embedding_text(
                        self.embedding_text_mode,
                        (
                        f"XML node {tag} in file {rel_path}. Path {path}. "
                        f"Attributes: {', '.join(attrs.keys()) or 'none'}. "
                        f"Children: {', '.join(child_tags[:20]) or 'none'}. "
                        f"Text: {text[:200] or 'none'}."
                        ),
                        (
                        f"XML node {tag}. Path {path}. "
                        f"Attrs {compact_list(list(attrs.keys()), limit=6)}. "
                        f"Children {compact_list(child_tags, limit=6)}. "
                        f"Text {compact_text(text, 100)}."
                        ),
                    ),
                })

        for tag, first_path in tag_first_seen.items():
            index_records.append({
                "kind": "xml_tag",
                "file": rel_path,
                "id": f"xml_tag:{safe_id(rel_path, tag)}",
                "tag": tag,
                "first_path": first_path,
                "attribute_names": sorted(tag_attrs[tag]),
                "child_tags": sorted(tag_children[tag]),
                "embedding_text": build_embedding_text(
                    self.embedding_text_mode,
                    (
                    f"XML tag {tag} in file {rel_path}. First path {first_path}. "
                    f"Attributes: {', '.join(sorted(tag_attrs[tag])) or 'none'}. "
                    f"Possible children: {', '.join(sorted(tag_children[tag])[:20]) or 'none'}."
                    ),
                    (
                    f"XML tag {tag}. Path {first_path}. "
                    f"Attrs {compact_list(sorted(tag_attrs[tag]), limit=6)}. "
                    f"Children {compact_list(sorted(tag_children[tag]), limit=6)}."
                    ),
                ),
            })

        return index_records, detail_records, relation_records
