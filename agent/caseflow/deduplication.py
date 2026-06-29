"""CaseFlow Deduplication — fingerprint and near-duplicate detection for DiscoveryResults."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from agent.caseflow.discovery import DiscoveryResult


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    method: str = "none"  # "exact_url" | "exact_fingerprint" | "near_duplicate" | "none"


def compute_fingerprint(
    result_type: str,
    title: str,
    source_url: Optional[str],
    normalized_payload: dict,
) -> str:
    """Compute a stable SHA1 fingerprint from normalized key values."""
    norm_title = re.sub(r"\s+", " ", title.strip().lower())
    url_part = ""
    if source_url:
        # Use scheme + host + path, ignore query params
        parts = source_url.split("?")[0].rstrip("/").lower()
        url_part = parts
    payload_keys = sorted(str(k) + "=" + str(v) for k, v in normalized_payload.items() if k != "raw_text")
    content = f"{result_type}|{norm_title}|{url_part}|{'|'.join(payload_keys)}"
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def check_duplicate(
    new_result: DiscoveryResult,
    existing_results: list[DiscoveryResult],
) -> DuplicateCheckResult:
    """Check if new_result is a duplicate of any existing result."""
    # 1. Exact URL match
    if new_result.source_url:
        for existing in existing_results:
            if existing.source_url and existing.source_url == new_result.source_url:
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_of=existing.id,
                    similarity=1.0,
                    method="exact_url",
                )

    # 2. Fingerprint match
    if new_result.fingerprint:
        for existing in existing_results:
            if existing.fingerprint and existing.fingerprint == new_result.fingerprint:
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_of=existing.id,
                    similarity=1.0,
                    method="exact_fingerprint",
                )

    # 3. Near-duplicate via token Jaccard similarity
    if new_result.title or new_result.raw_text:
        new_text = (new_result.title or "") + " " + (new_result.raw_text or "")
        for existing in existing_results:
            existing_text = (existing.title or "") + " " + (existing.raw_text or "")
            similarity = _jaccard_similarity(new_text, existing_text)
            if similarity >= 0.85:
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_of=existing.id,
                    similarity=similarity,
                    method="near_duplicate",
                )

    return DuplicateCheckResult(is_duplicate=False, method="none")


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
