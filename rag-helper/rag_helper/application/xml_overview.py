from __future__ import annotations


def build_xml_overview_records(index_records: list[dict], mode: str) -> list[dict]:
    if mode != "compact":
        return []

    records: list[dict] = []
    for record in index_records:
        if record.get("kind") != "xml_tag_summary":
            continue
        tags = list(record.get("tags", [])[:8])
        records.append({
            "kind": "xml_overview",
            "file": record.get("file"),
            "id": f"xml_overview:{record.get('id')}",
            "tag_count": record.get("tag_count", 0),
            "top_tags": [item.get("tag") for item in tags if item.get("tag")][:8],
            "paths": [item.get("first_path") for item in tags if item.get("first_path")][:4],
            "summary": (
                f"XML overview for {record.get('file')}. "
                f"Distinct tags={record.get('tag_count', 0)}. "
                f"Top tags: {', '.join(item.get('tag') for item in tags[:6] if item.get('tag')) or 'none'}."
            ),
        })
    return records
