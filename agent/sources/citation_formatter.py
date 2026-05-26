from __future__ import annotations

from typing import Any


def format_citation(
    *,
    descriptor: dict[str, Any],
    snapshot: dict[str, Any] | None,
    output_format: str = "long",
) -> dict[str, Any]:
    source_id = str(descriptor.get("source_id") or "")
    source_type = str(descriptor.get("source_type") or "")
    citation = dict(descriptor.get("citation_source") or {})
    latest_snapshot = dict(snapshot or {})
    snapshot_id = str(latest_snapshot.get("snapshot_id") or "none")
    snapshot_hash = str(latest_snapshot.get("content_hash") or "n/a")
    title = str(citation.get("title") or descriptor.get("display_name") or source_id)
    publisher = str(citation.get("publisher") or "unknown")
    canonical_url = str(citation.get("canonical_url") or "")
    retrieved_at = str(citation.get("retrieved_at") or latest_snapshot.get("retrieved_at") or "")
    license_ref = str(citation.get("license_ref") or "")
    version_label = str(citation.get("version_label") or "")
    language = str((descriptor.get("extensions") or {}).get("language_default") or "")
    short = f"{title} ({canonical_url})"
    long = (
        f"{title}. publisher={publisher}; url={canonical_url}; retrieved_at={retrieved_at}; "
        f"snapshot_id={snapshot_id}; snapshot_hash={snapshot_hash}; license={license_ref}"
    )
    if source_type == "keycloak_docs" and version_label:
        long += f"; version={version_label}"
    if source_type == "wikimedia_dump":
        if language:
            long += f"; language={language}"
        long += f"; attribution={citation.get('citation_text') or 'required'}"
    markdown = (
        f"- **Title:** {title}\n"
        f"- **Publisher:** {publisher}\n"
        f"- **URL:** {canonical_url}\n"
        f"- **Retrieved:** {retrieved_at}\n"
        f"- **Snapshot:** `{snapshot_id}` (`{snapshot_hash}`)\n"
        f"- **License:** {license_ref}\n"
    )
    payload = {
        "source_id": source_id,
        "source_type": source_type,
        "short": short,
        "long": long,
        "markdown": markdown,
        "json": {
            "title": title,
            "publisher": publisher,
            "canonical_url": canonical_url,
            "retrieved_at": retrieved_at,
            "snapshot_id": snapshot_id,
            "snapshot_hash": snapshot_hash,
            "license_ref": license_ref,
            "version_label": version_label,
            "language": language,
        },
    }
    selected = str(output_format or "long").lower()
    payload["rendered"] = payload.get(selected) if selected in {"short", "long", "markdown"} else payload["long"]
    return payload

