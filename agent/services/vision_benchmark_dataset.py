"""Validate frozen vision splits before spending inference or calibration budget."""

from __future__ import annotations

import hashlib
import json


def image_dataset_manifest(cases, *, maximum_cases: int = 120) -> dict:
    if type(maximum_cases) is not int or not 1 <= maximum_cases <= 4096:
        raise ValueError("vision_pilot_corpus_budget_invalid")
    if not cases or len(cases) > maximum_cases:
        raise ValueError("vision_pilot_corpus_size_invalid")
    partitions = {split: [] for split in ("train", "validation", "holdout")}
    identities, groups, images = set(), {}, {}
    for case in cases:
        if case.split not in partitions or case.language not in {"de", "en"}:
            raise ValueError("vision_pilot_corpus_partition_invalid")
        if not isinstance(case.group, str) or not case.group or not isinstance(case.variant, str) or not case.variant:
            raise ValueError("vision_pilot_corpus_identity_invalid")
        identity = (case.group, case.language, case.variant)
        if identity in identities:
            raise ValueError("vision_pilot_corpus_identity_duplicate")
        identities.add(identity)
        if not isinstance(case.image, bytes) or not case.image:
            raise ValueError("vision_pilot_corpus_image_invalid")
        digest = hashlib.sha256(case.image).hexdigest()
        if groups.setdefault(case.group, case.split) != case.split:
            raise ValueError("vision_pilot_corpus_group_overlap")
        if images.setdefault(digest, case.split) != case.split:
            raise ValueError("vision_pilot_corpus_image_overlap")
        partitions[case.split].append(
            {
                "group": case.group,
                "language": case.language,
                "variant": case.variant,
                "image_sha256": digest,
                "expected": case.expected,
            }
        )
    for split, rows in partitions.items():
        if {row["language"] for row in rows} != {"de", "en"}:
            raise ValueError("vision_pilot_corpus_language_missing")
        rows.sort(key=lambda row: (row["group"], row["language"], row["variant"]))
    encoded = json.dumps(partitions, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "schema": "ananta.vision-pilot-dataset.v1",
        "dataset_digest": hashlib.sha256(encoded.encode()).hexdigest(),
        "splits": {
            split: {"cases": len(rows), "groups": len({row["group"] for row in rows})}
            for split, rows in partitions.items()
        },
        "disjoint_groups": True,
        "disjoint_images": True,
    }
