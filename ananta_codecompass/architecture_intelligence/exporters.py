"""Portable export adapters. Cypher/GraphML are exports, not runtimes."""

from __future__ import annotations

import json
from typing import Any, Mapping


def export_json(result: Mapping[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True)


def export_markdown(result: Mapping[str, Any]) -> str:
    from ananta_codecompass.architecture_intelligence.wiki import render_wiki

    return render_wiki(result)


def export_graphml(projection: Mapping[str, Any]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<graphml><graph edgedefault="directed">']
    for node in projection.get("nodes") or []:
        lines.append(f'<node id="{_esc(node["id"])}"/>')
    for index, edge in enumerate(projection.get("edges") or []):
        lines.append(
            f'<edge id="e{index}" source="{_esc(edge["source"])}" target="{_esc(edge["target"])}"/>'
        )
    lines.append("</graph></graphml>")
    return "\n".join(lines)


def export_cypher(projection: Mapping[str, Any]) -> str:
    statements = []
    for node in projection.get("nodes") or []:
        statements.append(f"CREATE (:Node {{id:'{_esc(node['id'])}'}});")
    for edge in projection.get("edges") or []:
        statements.append(
            f"MATCH (a {{id:'{_esc(edge['source'])}'}}),(b {{id:'{_esc(edge['target'])}'}}) CREATE (a)-[:REL]->(b);"
        )
    return "\n".join(statements)


def export_obsidian(result: Mapping[str, Any]) -> str:
    lines = ["# Architecture", ""]
    for community in result.get("communities") or []:
        lines.append(f"## {community.get('label')}")
        for member in community.get("members") or []:
            lines.append(f"- [[{member}]]")
        lines.append("")
    return "\n".join(lines)


def export_html(result: Mapping[str, Any]) -> str:
    body = export_markdown(result).replace("\n", "<br/>\n")
    return f"<!doctype html><html><body>{body}</body></html>"


def _esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace("'", "")
