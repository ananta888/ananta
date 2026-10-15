"""Freeze multilingual article groups without granting dataset or release authority."""

from __future__ import annotations

import hashlib
import json


def freeze_articles(rows_by_language: dict, entities_by_language: dict, *, maximum_chars: int = 2000):
    if set(rows_by_language) != {"de", "en"} or set(entities_by_language) != {"de", "en"}:
        raise ValueError("growth_corpus_languages_required")
    if type(maximum_chars) is not int or not 100 <= maximum_chars <= 4000:
        raise ValueError("growth_corpus_text_budget_invalid")
    splits = {name: {"de": [], "en": []} for name in ("train", "validation", "holdout")}
    records, seen = [], set()
    for language in ("de", "en"):
        rows = rows_by_language[language]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise ValueError("growth_corpus_row_budget_invalid")
        for row in sorted(rows, key=lambda row: str(row["id"])):
            entity = entities_by_language[language].get(str(row["id"]))
            if not isinstance(entity, str) or not entity.startswith("Q") or not entity[1:].isdigit():
                continue  # Missing cross-language grouping is never inferred.
            text = str(row["text"]).strip()[:maximum_chars]
            if len(text) < 40:
                continue
            digest = hashlib.sha256(" ".join(text.casefold().split()).encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            bucket = int(hashlib.sha256(("growth-wiki-v1:" + entity).encode()).hexdigest()[:8], 16) % 10
            split = "train" if bucket < 6 else "validation" if bucket < 8 else "holdout"
            splits[split][language].append(text)
            records.append(
                {
                    "language": language,
                    "article_id": str(row["id"]),
                    "url": str(row["url"]),
                    "title": str(row["title"]),
                    "group": entity,
                    "split": split,
                    "content_sha256": digest,
                }
            )
    if any(len(texts) < 8 for languages in splits.values() for texts in languages.values()):
        raise ValueError("growth_corpus_insufficient_independent_articles")
    encoded = json.dumps(splits, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return splits, {
        "schema": "ananta.growth-corpus-freeze.v1",
        "classification": "external_dataset_technical_observation",
        "production_release_evidence": False,
        "grouping": "Wikidata entity; translations share the same split",
        "maximum_chars_per_article": maximum_chars,
        "dataset_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "records": records,
        "split_counts": {
            split: {language: len(texts) for language, texts in languages.items()}
            for split, languages in splits.items()
        },
    }
