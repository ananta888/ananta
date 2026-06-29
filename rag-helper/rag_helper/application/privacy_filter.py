"""Obsidian Privacy Filter (OBS-003)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag_helper.application.vault_scanner import VaultFile


@dataclass
class PrivacyResult:
    excluded: bool
    reason: str | None = None
    matched_mechanism: str | None = None  # "path_prefix" | "frontmatter" | "tag"


def _check_path_prefix(rel_path: str, prefixes: list[str]) -> bool:
    """Return True if rel_path starts with any of the given prefixes."""
    for prefix in prefixes:
        # Normalize: ensure prefix ends with / or matches exactly a dir name
        if rel_path.startswith(prefix):
            return True
    return False


def _check_frontmatter(frontmatter: dict, field_name: str, truthy_values: list) -> bool:
    """Return True if frontmatter[field_name] is in truthy_values."""
    if not frontmatter:
        return False
    val = frontmatter.get(field_name)
    if val is None:
        return False
    # Compare with truthy values, handling bool/str mismatch
    for tv in truthy_values:
        if val == tv:
            return True
        # Also compare as string
        if str(val).lower() == str(tv).lower():
            return True
    return False


def _check_tags(tags: list[str], private_tags: list[str]) -> bool:
    """Return True if any tag is in the private_tags list."""
    tag_set = {t.lower().lstrip("#") for t in tags}
    private_set = {t.lower().lstrip("#") for t in private_tags}
    return bool(tag_set & private_set)


def is_private(rel_path: str, frontmatter: dict | None, tags: list[str], profile) -> PrivacyResult:
    """
    Determine if a note should be excluded from indexing.

    profile must have: private_path_prefixes, private_frontmatter_field,
    private_frontmatter_truthy_values, private_tags, privacy_filter_mode
    """
    mode = getattr(profile, "privacy_filter_mode", "or")
    if mode == "off":
        return PrivacyResult(excluded=False)

    path_excluded = _check_path_prefix(rel_path, getattr(profile, "private_path_prefixes", []))
    frontmatter_excluded = _check_frontmatter(
        frontmatter or {},
        getattr(profile, "private_frontmatter_field", "private"),
        getattr(profile, "private_frontmatter_truthy_values", [True, "true", "yes", "1"]),
    )
    tag_excluded = _check_tags(tags, getattr(profile, "private_tags", []))

    if mode == "or":
        if path_excluded:
            return PrivacyResult(excluded=True, reason="path prefix match", matched_mechanism="path_prefix")
        if frontmatter_excluded:
            return PrivacyResult(excluded=True, reason="frontmatter private flag", matched_mechanism="frontmatter")
        if tag_excluded:
            return PrivacyResult(excluded=True, reason="private tag", matched_mechanism="tag")
        return PrivacyResult(excluded=False)

    if mode == "and":
        # All active mechanisms must fire
        active_checks = []
        if getattr(profile, "private_path_prefixes", []):
            active_checks.append(path_excluded)
        if getattr(profile, "private_frontmatter_field", None):
            active_checks.append(frontmatter_excluded)
        if getattr(profile, "private_tags", []):
            active_checks.append(tag_excluded)
        if not active_checks:
            return PrivacyResult(excluded=False)
        if all(active_checks):
            return PrivacyResult(excluded=True, reason="all privacy mechanisms matched", matched_mechanism="and")
        return PrivacyResult(excluded=False)

    return PrivacyResult(excluded=False)


def list_excluded(vault_profile, vault_files: list) -> list[dict]:
    """
    Return a list of dicts describing each excluded file and why.

    vault_files: list of VaultFile objects (from vault_scanner.scan())
    """
    from rag_helper.extractors.obsidian import parse_frontmatter, extract_tags

    excluded = []
    for vf in vault_files:
        try:
            with open(vf.abs_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""

        if vf.ext == "canvas":
            # Canvas files are never private by content
            result = is_private(vf.rel_path, {}, [], vault_profile)
        else:
            frontmatter, body = parse_frontmatter(text)
            tags = extract_tags(frontmatter, body)
            result = is_private(vf.rel_path, frontmatter, tags, vault_profile)

        if result.excluded:
            excluded.append({
                "rel_path": vf.rel_path,
                "reason": result.reason,
                "mechanism": result.matched_mechanism,
            })
    return excluded
