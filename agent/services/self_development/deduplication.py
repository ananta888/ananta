"""Content-based duplicate detection for the self-development backlog.

Imported TODO documents can carry repeated entries for one and the same work
item (generated planning batches). Every duplicate burns a dispatch, an
acceptance review and attempt budget on identical work and can even publish
the same change twice, so the backlog consolidates around one canonical item
per content signature. Pure functions only: the coordinator owns all
mutations (SRP).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

STATUS_RANK: dict[str, int] = {
    "published": 8,
    "review_passed": 7,
    "pending_review": 6,
    "running": 5,
    "publication_failed": 4,
    "ready": 3,
    "failed": 2,
    "blocked": 1,
    "skipped": 0,
}

_WHITESPACE = re.compile(r"\s+")


def _normalize_text(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip().lower()


def content_signature(item: dict) -> str:
    """Digest over the work-defining content of one backlog item.

    Titles, description, acceptance criteria, the document goal and the
    declared files define the work. Technical bookkeeping (keys, attempts,
    statuses, review artifacts) must not split one task into several groups.
    """
    source_execution = item.get("source_execution") if isinstance(item.get("source_execution"), dict) else {}
    files = source_execution.get("files") or []
    basis = {
        "title": _normalize_text(item.get("title")),
        "description": _normalize_text(item.get("description")),
        "criteria": [
            _normalize_text(entry) for entry in (item.get("acceptance_criteria") or []) if isinstance(entry, str)
        ],
        "document_goal": _normalize_text(item.get("document_goal")),
        "files": sorted(_normalize_text(path) for path in files if isinstance(path, str)),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()


def canonical_of(members: list[dict]) -> dict:
    """The member whose work is furthest along; deterministic tie-break."""

    def rank(item: dict) -> tuple[int, str]:
        return (-STATUS_RANK.get(str(item.get("status") or ""), -1), str(item.get("todo_id") or ""))

    return min(members, key=rank)


def group_duplicates(items: list[dict]) -> list[dict]:
    """Groups of live items that describe the same work (>= 2 members).

    Each group names the canonical item and the duplicates to fold into it.
    Cancelled items never count: their work is already out of the pipeline.
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        groups.setdefault(content_signature(item), []).append(item)
    result: list[dict] = []
    for signature, members in sorted(groups.items()):
        live = [member for member in members if str(member.get("status") or "") != "cancelled"]
        if len(live) < 2:
            continue
        canonical = canonical_of(live)
        result.append(
            {
                "signature": signature[:12],
                "canonical": canonical,
                "duplicates": [member for member in live if member is not canonical],
            }
        )
    return result


def signature_owner_map(items: dict[str, dict]) -> dict[str, str]:
    """signature -> key of the live item that already owns that content."""
    owners: dict[str, str] = {}
    for key, item in items.items():
        if not isinstance(item, dict) or str(item.get("status") or "") == "cancelled":
            continue
        owners.setdefault(content_signature(item), str(item.get("key") or key))
    return owners
