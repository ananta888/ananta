#!/usr/bin/env python3
"""Download bounded, pinned public benchmarks; never admit production evidence."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.services.public_decision_datasets import DOCLAYNET_REVISION, MASSIVE_REVISION, freeze_massive


def download(url, maximum_bytes):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"huggingface.co", "datasets-server.huggingface.co"}:
        raise ValueError("public_dataset_origin_invalid")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read(maximum_bytes + 1)
                revision = response.headers.get("x-revision")
            break
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                raise
            time.sleep(0.25 * (attempt + 1))
    if len(body) > maximum_bytes:
        raise ValueError("public_dataset_download_budget")
    return body, revision


def freeze_intents(directory, per_label):
    import pyarrow as arrow
    import pyarrow.parquet as parquet

    rows, sources = {}, []
    for source_split, split in (("validation", "validation"), ("test", "holdout")):
        rows[split] = {}
        for locale, language in (("de-DE", "de"), ("en-US", "en")):
            url = (
                f"https://huggingface.co/datasets/AmazonScience/massive/resolve/{MASSIVE_REVISION}/"
                f"{locale}/massive-{source_split}.parquet"
            )
            content, _ = download(url, 8 * 1024 * 1024)
            table = parquet.read_table(arrow.BufferReader(content))
            names = json.loads(table.schema.metadata[b"huggingface"])["info"]["features"]["intent"]["names"]
            rows[split][language] = [
                {"id": row["id"], "text": row["utt"], "label": names[row["intent"]]} for row in table.to_pylist()
            ]
            sources.append({"url": url, "sha256": hashlib.sha256(content).hexdigest()})
    corpus = freeze_massive(rows, per_label=per_label)
    corpus["sources"] = sources
    (directory / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + "\n")
    manifest = {key: value for key, value in corpus.items() if key != "records"}
    manifest["records"] = [{key: value for key, value in row.items() if key != "text"} for row in corpus["records"]]
    manifest["corpus_file_sha256"] = hashlib.sha256((directory / "corpus.json").read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return len(corpus["records"])


def freeze_documents(directory, documents):
    import pyarrow.parquet as parquet
    from huggingface_hub import HfFileSystem

    filesystem = HfFileSystem(token=False)
    selected, used_documents, projections = [], set(), []
    for source_split, split, target in (
        ("train", "train", 8),
        ("val", "validation", documents),
        ("test", "holdout", documents),
    ):
        files = sorted(
            filesystem.glob(
                f"datasets/docling-project/DocLayNet-v1.1@{DOCLAYNET_REVISION}/data/{source_split}-*.parquet"
            )
        )
        if not files or len(files) > 32:
            raise ValueError("doclaynet_source_file_budget")
        # Training is identity-only here; never used to fit a profile.
        if split == "train":
            files = files[:1]
        candidates, row_offset = [], 0
        for filename in files:
            with filesystem.open(filename, "rb", block_size=65536) as stream:
                rows = parquet.ParquetFile(stream).read(columns=["metadata", "category_id"]).to_pylist()
            projections.append(
                {
                    "file": filename,
                    "columns": ["metadata", "category_id"],
                    "rows": len(rows),
                    "projection_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                }
            )
            for index, row in enumerate(rows):
                meta = row["metadata"]
                candidates.append(
                    {
                        "document": meta["original_filename"],
                        "split": split,
                        "source_split": source_split,
                        "source_row": row_offset + index,
                        "page_hash": meta["page_hash"],
                        "document_category": meta["doc_category"],
                        "collection": meta["collection"],
                        "has_table": 9 in row["category_id"],
                        "has_picture": 7 in row["category_id"],
                    }
                )
            row_offset += len(rows)
        counts = {True: 0, False: 0}
        for row in sorted(candidates, key=lambda row: row["page_hash"]):
            if row["document"] in used_documents or counts[row["has_table"]] >= target // 2:
                continue
            used_documents.add(row["document"])
            counts[row["has_table"]] += 1
            selected.append(row)
            if sum(counts.values()) == target:
                break
        if sum(counts.values()) != target:
            raise ValueError(f"doclaynet_insufficient_independent_documents:{split}:{counts}")
        print(json.dumps({"split": split, "selected_documents": target}), file=sys.stderr, flush=True)

    def save(row):
        row = dict(row)
        url = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode(
            dict(
                dataset="docling-project/DocLayNet-v1.1",
                config="default",
                split=row["source_split"],
                offset=row["source_row"],
                length=1,
            )
        )
        content, revision = download(url, 2 * 1024 * 1024)
        original = json.loads(content)["rows"][0]["row"]
        if (
            revision != DOCLAYNET_REVISION
            or original["metadata"]["page_hash"] != row["page_hash"]
            or original["metadata"]["original_filename"] != row["document"]
            or original["metadata"]["doc_category"] != row["document_category"]
            or (9 in original["category_id"]) != row["has_table"]
            or (7 in original["category_id"]) != row["has_picture"]
        ):
            raise ValueError("doclaynet_image_projection_mismatch")
        image_url = original["image"]["src"]
        content, _ = download(image_url, 2 * 1024 * 1024)
        digest = hashlib.sha256(content).hexdigest()
        (directory / (digest + ".jpg")).write_bytes(content)
        row.update(image_sha256=digest, image_origin=urllib.parse.urlsplit(image_url)._replace(query="").geturl())
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(save, selected))
    manifest = {
        "schema": "ananta.doclaynet-public-freeze.v1",
        "source_revision": DOCLAYNET_REVISION,
        "declared_license": "CDLA-Permissive-1.0",
        "attribution": "IBM Research / DocLayNet contributors",
        "license_reference": f"https://huggingface.co/datasets/docling-project/DocLayNet-v1.1/blob/{DOCLAYNET_REVISION}/README.md",
        "classification": "external_dataset_technical_observation",
        "production_release_evidence": False,
        "language_scope": "DE/EN prompt languages; document language is not independently verified",
        "sampling": "Original train/val/test; page-hash order; one page per globally distinct filename; balanced table labels",
        "metadata_sources": projections,
        "records": records,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["massive", "doclaynet"])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--per-label", type=int, default=15)
    parser.add_argument("--documents", type=int, default=100)
    args = parser.parse_args()
    if not 20 <= args.documents <= 200 or args.documents % 2:
        parser.error("--documents requires an even integer in [20,200]")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    count = (
        freeze_intents(args.output_dir, args.per_label)
        if args.kind == "massive"
        else freeze_documents(args.output_dir, args.documents)
    )
    print(json.dumps({"records": count, "production_release_evidence": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
