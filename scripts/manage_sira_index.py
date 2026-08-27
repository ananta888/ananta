#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worker.retrieval.sira.contracts import CorpusBinding
from worker.retrieval.sira.enriched_fts_store import EnrichedFtsStore
from worker.retrieval.sira.incremental_enrichment import EnrichmentLayerStore


def _json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _binding(args: argparse.Namespace) -> CorpusBinding:
    return CorpusBinding(
        tenant_id=args.tenant_id,
        scope=args.scope,
        repository_revision=args.repository_revision,
        source_manifest_hash=args.source_manifest_hash,
        index_digest=args.index_digest,
        statistics_digest=args.statistics_digest,
        profile_version=args.profile_version,
        base_layer_id=getattr(args, "base_layer_id", ""),
        delta_layer_ids=tuple(getattr(args, "delta_layer_id", []) or []),
    )


def _add_binding(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--repository-revision", required=True)
    parser.add_argument("--source-manifest-hash", required=True)
    parser.add_argument("--index-digest", required=True)
    parser.add_argument("--statistics-digest", required=True)
    parser.add_argument("--profile-version", default="corpus-discriminative-lexical.v1")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and inspect a bound CodeCompass SIRA index.")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Atomically rebuild the enriched FTS snapshot.")
    build.add_argument("--db", required=True)
    build.add_argument("--documents", required=True)
    build.add_argument("--enrichments", required=True)
    _add_binding(build)
    diagnostics = commands.add_parser("diagnostics", help="Read the active snapshot status.")
    diagnostics.add_argument("--db", required=True)
    _add_binding(diagnostics)
    compact = commands.add_parser("compact", help="Compact active base/delta enrichment layers.")
    compact.add_argument("--layer-root", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "compact":
        result = EnrichmentLayerStore(root=args.layer_root).compact()
    else:
        store = EnrichedFtsStore(db_path=args.db)
        binding = _binding(args)
        if args.command == "diagnostics":
            result = store.diagnostics(binding=binding)
        else:
            documents = _json(args.documents)
            enrichments = _json(args.enrichments)
            if not isinstance(documents, list) or not isinstance(enrichments, dict):
                raise ValueError("sira_index_input_invalid")
            result = store.rebuild(documents=documents, enrichments=enrichments, binding=binding)
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, indent=2))
    return 0 if str(result.get("status") or "ok") in {"ok", "ready"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
