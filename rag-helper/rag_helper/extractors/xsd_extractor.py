from __future__ import annotations

from typing import Any

from lxml import etree
from rag_helper.utils.ids import safe_id


class XsdExtractor:
    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        root = etree.fromstring(text.encode("utf-8", errors="ignore"), parser=parser)

        namespaces = dict(root.nsmap) if root.nsmap else {}
        root_tag = self._strip_ns(root.tag)

        index_records = [{
            "kind": "xsd_file",
            "file": rel_path,
            "id": f"xsd_file:{safe_id(rel_path)}",
            "root": root_tag,
            "namespaces": namespaces,
            "embedding_text": (
                f"XSD file {rel_path}. Root tag {root_tag}. "
                f"Namespaces: {', '.join([f'{k}={v}' for k, v in namespaces.items()][:10]) or 'none'}."
            ),
        }]
        detail_records: list[dict] = []
        relation_records: list[dict] = []

        idx, det, rel = self._extract_xsd(rel_path, root)
        index_records.extend(idx)
        detail_records.extend(det)
        relation_records.extend(rel)

        stats = {
            "kind": "xsd",
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

    def _extract_xsd(self, rel_path: str, root) -> tuple[list[dict], list[dict], list[dict]]:
        ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
        index_records = []
        detail_records = []
        relation_records = []

        for elem in root.xpath(".//xs:complexType", namespaces=ns):
            name = elem.get("name")
            if not name:
                continue
            ct_id = f"xsd_complex_type:{safe_id(rel_path, name)}"

            child_elements = []
            attrs = []
            bases = []

            for e in elem.xpath(".//xs:element", namespaces=ns):
                child_elements.append({
                    "name": e.get("name"),
                    "type": e.get("type"),
                    "ref": e.get("ref"),
                    "minOccurs": e.get("minOccurs"),
                    "maxOccurs": e.get("maxOccurs"),
                })

            for a in elem.xpath(".//xs:attribute", namespaces=ns):
                attrs.append({
                    "name": a.get("name"),
                    "type": a.get("type"),
                    "use": a.get("use"),
                })

            for ex in elem.xpath(".//xs:extension", namespaces=ns):
                if ex.get("base"):
                    bases.append(ex.get("base"))

            index_records.append({
                "kind": "xsd_complex_type",
                "file": rel_path,
                "id": ct_id,
                "name": name,
                "elements": [
                    f"{x.get('name') or x.get('ref')}:{x.get('type')}" for x in child_elements[:50]
                ],
                "attributes": [
                    f"{x.get('name')}:{x.get('type')}" for x in attrs[:50]
                ],
                "extends": bases[:10],
                "embedding_text": (
                    f"XSD complex type {name} in file {rel_path}. "
                    f"Elements: {', '.join([f'{x.get('name') or x.get('ref')}:{x.get('type')}' for x in child_elements[:20]]) or 'none'}. "
                    f"Attributes: {', '.join([f'{x.get('name')}:{x.get('type')}' for x in attrs[:20]]) or 'none'}. "
                    f"Extends: {', '.join(bases[:10]) or 'none'}."
                ),
            })

            detail_records.append({
                "kind": "xsd_complex_type_detail",
                "file": rel_path,
                "id": f"xsd_complex_type_detail:{safe_id(rel_path, name)}",
                "name": name,
                "elements": child_elements,
                "attributes": attrs,
                "extends": bases,
            })

            for el in child_elements:
                if el.get("type"):
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=ct_id,
                        source_kind="xsd_complex_type",
                        source_name=name,
                        relation="contains_element_type",
                        target=el.get("name") or el.get("ref") or "",
                        target_resolved=el.get("type"),
                    ))
                elif el.get("ref"):
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=ct_id,
                        source_kind="xsd_complex_type",
                        source_name=name,
                        relation="contains_element_ref",
                        target=el.get("ref"),
                        target_resolved=el.get("ref"),
                    ))

            for at in attrs:
                if at.get("type"):
                    relation_records.append(self._make_relation(
                        file=rel_path,
                        source_id=ct_id,
                        source_kind="xsd_complex_type",
                        source_name=name,
                        relation="has_attribute_type",
                        target=at.get("name") or "",
                        target_resolved=at.get("type"),
                    ))

            for base in bases:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=ct_id,
                    source_kind="xsd_complex_type",
                    source_name=name,
                    relation="extends",
                    target=base,
                    target_resolved=base,
                ))

        for elem in root.xpath(".//xs:simpleType", namespaces=ns):
            name = elem.get("name")
            if not name:
                continue
            restrictions = [r.get("base") for r in elem.xpath(".//xs:restriction", namespaces=ns) if r.get("base")]
            index_records.append({
                "kind": "xsd_simple_type",
                "file": rel_path,
                "id": f"xsd_simple_type:{safe_id(rel_path, name)}",
                "name": name,
                "restrictions": restrictions[:20],
                "embedding_text": (
                    f"XSD simple type {name} in file {rel_path}. "
                    f"Restrictions: {', '.join(restrictions[:20]) or 'none'}."
                ),
            })
            for base in restrictions:
                relation_records.append(self._make_relation(
                    file=rel_path,
                    source_id=f"xsd_simple_type:{safe_id(rel_path, name)}",
                    source_kind="xsd_simple_type",
                    source_name=name,
                    relation="restricted_by",
                    target=base,
                    target_resolved=base,
                ))

        for elem in root.xpath("./xs:element", namespaces=ns):
            name = elem.get("name")
            if not name:
                continue
            index_records.append({
                "kind": "xsd_root_element",
                "file": rel_path,
                "id": f"xsd_root_element:{safe_id(rel_path, name)}",
                "name": name,
                "type": elem.get("type"),
                "ref": elem.get("ref"),
                "embedding_text": (
                    f"XSD root element {name} in file {rel_path}. "
                    f"Type {elem.get('type') or 'none'}. Ref {elem.get('ref') or 'none'}."
                ),
            })

        return index_records, detail_records, relation_records
