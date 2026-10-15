#!/usr/bin/env python3
"""Explicit bounded download; productive use still requires Hub source admission."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker.training.growth.corpus import freeze_articles

REVISION = "b04c8d1ceb2f5cd4588862100d08de323dccfbaa"


def read_json(url, *, maximum_bytes=8 * 1024 * 1024):
    request = urllib.request.Request(url, headers={"User-Agent": "AnantaResearchEvaluation/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(maximum_bytes + 1)
        revision = response.headers.get("x-revision")
    if len(payload) > maximum_bytes:
        raise ValueError("growth_corpus_download_budget")
    return json.loads(payload), revision, hashlib.sha256(payload).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    rows, entities, sources = {}, {}, []
    for language in ("de", "en"):
        url = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode(
            {
                "dataset": "wikimedia/wikipedia",
                "config": "20231101." + language,
                "split": "train",
                "offset": 20000,
                "length": 100,
            }
        )
        payload, revision, digest = read_json(url)
        if revision != REVISION:
            raise ValueError("growth_corpus_source_revision_changed")
        rows[language] = [entry["row"] for entry in payload["rows"]]
        entities[language] = {}
        sources.append({"url": url, "revision": revision, "response_sha256": digest})
        identifiers = [str(row["id"]) for row in rows[language]]
        for offset in range(0, len(identifiers), 50):
            url = f"https://{language}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {
                    "action": "query",
                    "prop": "pageprops",
                    "ppprop": "wikibase_item",
                    "pageids": "|".join(identifiers[offset : offset + 50]),
                    "format": "json",
                }
            )
            mapping, _, digest = read_json(url)
            sources.append({"url": url, "response_sha256": digest})
            for page_id, page in mapping["query"]["pages"].items():
                entities[language][page_id] = page.get("pageprops", {}).get("wikibase_item")
    splits, manifest = freeze_articles(rows, entities)
    manifest.update(
        source_dataset="wikimedia/wikipedia",
        source_revision=REVISION,
        sources=sources,
        declared_license="CC-BY-SA-3.0",
        attribution="Wikipedia contributors; article URLs are retained per record",
        license_reference="https://huggingface.co/datasets/wikimedia/wikipedia/blob/" + REVISION + "/README.md",
    )
    args.output.write_text(json.dumps(splits, ensure_ascii=False, indent=2) + "\n")
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"split_counts": manifest["split_counts"], "production_release_evidence": False}))


if __name__ == "__main__":
    main()
