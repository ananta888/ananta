"""Deterministic Merkle roots and content-free inventory diffs."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Iterable, Mapping

EMPTY_ROOT = hashlib.sha256(b"ananta.speech-evidence-merkle.empty.v1").hexdigest()


@dataclass(frozen=True)
class EvidenceMerkleDiff:
    base_root_digest: str
    target_root_digest: str
    missing_group_ids: tuple[str, ...]
    changed_group_ids: tuple[str, ...]
    cursor_digest: str
    complete: bool
    total_groups: int


def merkle_root(leaves: Iterable[tuple[str, str]]) -> str:
    """Hash sorted opaque ID/digest pairs without accessing evidence payloads."""

    level = [
        hashlib.sha256(f"leaf\0{group_id}\0{digest}".encode("ascii")).digest()
        for group_id, digest in sorted(leaves)
    ]
    if not level:
        return EMPTY_ROOT
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(b"node\0" + level[index] + level[index + 1]).digest()
            for index in range(0, len(level), 2)
        ]
    return level[0].hex()


def diff_inventories(
    base: Mapping[str, str],
    target: Mapping[str, str],
    *,
    pair_key: bytes,
    scope_digest: str,
    consent_version: int,
    epoch: int,
    maximum_groups: int = 4096,
) -> EvidenceMerkleDiff:
    if len(pair_key) < 32 or not 1 <= maximum_groups <= 4096:
        raise ValueError("speech_evidence_merkle_policy_invalid")
    base_root = merkle_root(base.items())
    target_root = merkle_root(target.items())
    missing_all = sorted(group_id for group_id in target if group_id not in base)
    changed_all = sorted(
        group_id for group_id in target if group_id in base and target[group_id] != base[group_id]
    )
    page = [*(('missing', item) for item in missing_all), *(('changed', item) for item in changed_all)]
    selected = page[:maximum_groups]
    missing = tuple(item for kind, item in selected if kind == "missing")
    changed = tuple(item for kind, item in selected if kind == "changed")
    cursor = hmac.new(
        pair_key,
        (
            "ananta.speech-evidence-cursor.v1\0"
            f"{base_root}\0{target_root}\0{scope_digest}\0{consent_version}\0{epoch}\0"
            f"{len(selected)}"
        ).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return EvidenceMerkleDiff(
        base_root_digest=base_root,
        target_root_digest=target_root,
        missing_group_ids=missing,
        changed_group_ids=changed,
        cursor_digest=cursor,
        complete=len(selected) == len(page),
        total_groups=len(page),
    )


__all__ = ["EMPTY_ROOT", "EvidenceMerkleDiff", "diff_inventories", "merkle_root"]
