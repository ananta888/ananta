from __future__ import annotations

from collections import defaultdict
from typing import Any

from lxml import etree
from rag_helper.utils.ids import safe_id


class XmlExtractor:
    def __init__(self, include_xml_node_details: bool = True) -> None:
        self.include_xml_node_details = include_xml_node_details

    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        root = etree.fromstring(text.encode("utf-8", errors="ignore"), parser=parser)

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
            "embedding_text": (
                f"XML file {rel_path}. Root tag {root_tag}. "
                f"Namespaces: {', '.join([f'{k}={v}' for k, v in namespaces.items()][:10]) or 'none'}."
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
            "index_count": len(index_records),
            "detail_count": len(detail_records),
            "relation_count": len(relation_records),
        }
        return index_records, detail_records, relation_records, stats

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
                    "embedding_text": (
                        f"XML node {tag} in file {rel_path}. Path {path}. "
                        f"Attributes: {', '.join(attrs.keys()) or 'none'}. "
                        f"Children: {', '.join(child_tags[:20]) or 'none'}. "
                        f"Text: {text[:200] or 'none'}."
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
                "embedding_text": (
                    f"XML tag {tag} in file {rel_path}. First path {first_path}. "
                    f"Attributes: {', '.join(sorted(tag_attrs[tag])) or 'none'}. "
                    f"Possible children: {', '.join(sorted(tag_children[tag])[:20]) or 'none'}."
                ),
            })

        return index_records, detail_records, relation_records
