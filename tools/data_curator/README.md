Data Acquisition & Curation Tool (SPDX Whitelist)

Overview
- Collects, validates, parses, deduplicates, chunks, tags, and exports datasets for LLM training from whitelist-only licenses.
- Strict SPDX whitelist: GPL-2.0/3.0, LGPL-2.1/3.0, AGPL-3.0, BSD-2/3, MIT, Apache-2.0, MPL-2.0. Drops anything else (including CC and unclear).
- Provenance per file: source_url, commit, sha256, swhid (optional).
- Parsers: code (optional AST via tree-sitter if available), Markdown/ReST (tables & code blocks separated), plain text.
- Dedup: exact (SHA) + near-dup (SimHash) over blocks.
- Chunking: semantic by blocks, 512–2048 tokens.
- PII/Safety: simple heuristics + configurable block patterns (policy.yaml).
- Tagging: language, file type, coarse domain labels.
- Export: JSONL + Manifest (sources, license histogram, metrics), optional Parquet (if pyarrow installed).
- Deterministic: sorted processing, seed in steps and manifest; dataset hash in manifest.

Install
- Uses only stdlib; optional: PyYAML, tree_sitter_languages, pyarrow.

Usage (Windows PowerShell examples)
- Initialize templates (creates sources.yaml, policy.yaml for PII, and license_policy.yaml for license enforcement):
  python -m tools.data_curator.tool init --out .

- Crawl local sources (copy files to raw/) with policy pre-gate:
  python -m tools.data_curator.tool crawl --sources sources.yaml --out raw --policy license_policy.yaml

- Post-fetch license scan and stage compliant files (policy enforcement, conflict handling, audit):
  python -m tools.data_curator.tool license-scan --in raw --out staged --policy license_policy.yaml

- Parse to typed blocks:
  python -m tools.data_curator.tool parse --in staged --out parsed

- Deduplicate (exact + near):
  python -m tools.data_curator.tool dedup --in parsed --out unique --near-threshold 3 --seed 42

- Chunk with PII/policy filtering:
  python -m tools.data_curator.tool chunk --in unique --out chunks --policy policy.yaml --min-tokens 512 --max-tokens 2048

- Tag:
  python -m tools.data_curator.tool tag --in chunks --out tagged

- Export JSONL and Manifest (and Parquet if available):
  python -m tools.data_curator.tool export --in tagged --out data\dataset.jsonl --manifest data\MANIFEST.json --sources sources.yaml
  # Optional Parquet
  python -m tools.data_curator.tool export --in tagged --out data\dataset.jsonl --manifest data\MANIFEST.json --sources sources.yaml --parquet data\dataset.parquet

- Report:
  python -m tools.data_curator.tool report --manifest data\MANIFEST.json --out reports

Notes
- Only local sources are supported out of the box. You can add git support via external tooling and point to checked-out directories.
- The tool never generates synthetic text.
- All non-whitelist or unclear licenses are dropped at pre- and post-fetch stages with logs in data_curator.logs/.
