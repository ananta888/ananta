#!/usr/bin/env python3
"""Index the Ananta project codebase into the Knowledge/CodeCompass system.

Usage:
    python scripts/setup_codecompass_index.py
    python scripts/setup_codecompass_index.py --hub http://localhost:5000 --user admin --password test123
    python scripts/setup_codecompass_index.py --dry-run   # show what would be indexed

This script:
  1. Scans the Ananta repo for Python, TypeScript, and markdown files
  2. POSTs them as records to /knowledge/sources/index-records
  3. The snake chat (CodeCompass) can then retrieve real project context
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INCLUDE_EXTENSIONS = {".py", ".ts", ".md", ".json", ".yaml", ".yml"}
EXCLUDE_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv",
    "dist", "build", ".angular", "test-results", ".claude",
    "migrations", "alembic",
}
EXCLUDE_FILE_PATTERNS = {"*.spec.ts", "*.test.py", "*.pyc", "package-lock.json"}
MAX_FILE_BYTES = 48_000  # skip files larger than this
MAX_RECORDS = 2000       # hard cap to avoid overwhelming the indexer
PRIORITY_DIRS = [        # indexed first, guaranteed inclusion
    "agent/routes",
    "agent/services",
    "frontend-angular/src/app/components",
    "frontend-angular/src/app/services",
    "agent",
    "client_surfaces",
]

# Semantic aliases: file path fragments → tags added to the content header.
# This bridges the gap between project-internal names and user-facing terms so
# embedding queries like "codecompass" find the right Python files.
_PATH_TAGS: list[tuple[str, str]] = [
    ("codecompass",          "CodeCompass RAG retrieval system knowledge index"),
    ("knowledge_index",      "CodeCompass knowledge index retrieval RAG"),
    ("retrieval_service",    "CodeCompass RAG retrieval orchestration"),
    ("context_bundle",       "CodeCompass context bundle RAG assembly grounded prompt"),
    ("worker_workspace",     "worker workspace preparation CodeCompass context files"),
    ("knowledge_routes",     "CodeCompass knowledge API routes"),
    ("snake",                "AI-Snake chat CodeCompass question answering"),
    ("goal_service",         "goal task planning execution service"),
    ("planning_strategies",  "planning strategy task decomposition"),
    ("task_scoped_execution","task execution worker proposal pipeline"),
    ("sgpt",                 "LLM CLI backend ananta-worker execution"),
]

# A synthetic glossary record injected first so the embedding index always
# contains the project terminology mapping even before any file is indexed.
_GLOSSARY_RECORD = {
    "file": "_project_glossary.md",
    "path": "_project_glossary.md",
    "content": """\
# Ananta Project Glossary — CodeCompass & RAG System

## CodeCompass
CodeCompass ist das RAG-System (Retrieval Augmented Generation) von Ananta.
Es indiziert Quellcode-Dateien und liefert bei Anfragen die semantisch relevantesten
Datei-Inhalte als Kontext für LLM-Anfragen (AI-Snake Chat, ananta-worker, OpenCode).

Kern-Python-Dateien von CodeCompass:
- agent/services/codecompass_retrieval_flag_service.py  — steuert aktive CodeCompass-Quellen
- agent/services/codecompass_output_reader.py            — liest CodeCompass-Ausgaben
- agent/services/wiki_codecompass_bridge.py              — Wiki-Integration
- agent/services/knowledge_index_retrieval_service.py    — Hauptlogik Knowledge-Index-Abfrage
- agent/services/knowledge_index_job_service.py          — Indizierungs-Jobs verwalten
- agent/services/retrieval_service.py                    — RAG-Orchestrierung aller Engines
- agent/services/context_bundle_service.py               — baut den RAG-Kontext-Bundle zusammen
- agent/services/prompt_context_bundle_service.py        — Prompt-Kontext-Aufbereitung
- agent/services/worker_workspace_service.py             — bereitet Workspace für Worker vor
- agent/routes/knowledge.py                              — REST-API für Knowledge/CodeCompass

## Knowledge Index
Der Knowledge Index speichert Datei-Inhalte als Vektoren (Embeddings).
Queries liefern die N ähnlichsten Chunks zurück (top_k).
Source scope "artifact" = indizierte Projekt-Dateien.

## RAG (Retrieval Augmented Generation)
Vor jeder LLM-Anfrage werden relevante Datei-Inhalte aus dem Index geholt
und in den Prompt eingebettet, damit das Modell projektspezifische Fragen
beantworten kann.

## AI-Snake Chat
Der AI-Snake Chat (/snake/ask) nutzt CodeCompass: Die Nutzeranfrage wird
gegen den Knowledge Index abgeglichen, relevante Dateien werden als Kontext
übergeben, dann antwortet das LLM.
""",
}


def _should_exclude_file(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    if any(path.match(pat) for pat in EXCLUDE_FILE_PATTERNS):
        return True
    return False


def _collect_files() -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []

    def _add(p: Path) -> None:
        if p in seen or p not in p.parents[0].iterdir().__class__:
            return
        if not p.exists() or p in seen:
            return
        seen.add(p)
        result.append(p)

    # Priority dirs first
    for rel in PRIORITY_DIRS:
        d = ROOT / rel
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.suffix in INCLUDE_EXTENSIONS and not _should_exclude_file(f):
                if f not in seen:
                    seen.add(f)
                    result.append(f)

    # Then rest of repo
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix not in INCLUDE_EXTENSIONS:
            continue
        if _should_exclude_file(f):
            continue
        if any(part in EXCLUDE_DIRS for part in f.parts):
            continue
        if f not in seen:
            seen.add(f)
            result.append(f)

    return result[:MAX_RECORDS]


def _file_tags(rel: str) -> str:
    """Return extra semantic tag tokens for a file based on path keywords."""
    tags: list[str] = []
    for fragment, tag in _PATH_TAGS:
        if fragment in rel:
            tags.append(tag)
    return " | ".join(tags)


def _build_records(files: list[Path]) -> list[dict]:
    # Glossary record first so it is always included even if MAX_RECORDS is tight.
    records: list[dict] = [_GLOSSARY_RECORD]
    for f in files:
        try:
            size = f.stat().st_size
            if size > MAX_FILE_BYTES:
                continue
            content = f.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            rel = str(f.relative_to(ROOT))
            tags = _file_tags(rel)
            # Prepend path + semantic tags so embeddings include project terminology.
            header = f"# {rel}"
            if tags:
                header += f"\n# Themen: {tags}"
            content_with_header = f"{header}\n{content}"
            records.append({"file": rel, "path": rel, "content": content_with_header})
        except Exception:
            continue
    return records


def _build_semantic_translation_records(files: list[Path]) -> tuple[list[dict], dict]:
    from agent.codecompass.semantic_translation.adapters import JavaSemanticAdapter
    from agent.codecompass.semantic_translation.config import load_semantic_translation_config
    from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRuleRegistry

    config = load_semantic_translation_config()
    if not config.enabled:
        return [], {"enabled": False, "warnings": list(config.diagnostics)}
    adapter = JavaSemanticAdapter()
    records: list[dict] = []
    diagnostics: list[dict] = []
    analyzed_files = 0
    for path in files:
        if path.suffix != ".java":
            continue
        rel = str(path.relative_to(ROOT))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            diagnostics.append({"code": "semantic_file_read_failed", "path": rel, "message": str(exc)})
            continue
        if not adapter.detect(rel, content):
            continue
        analyzed_files += 1
        emitted = adapter.emit_graph_records(rel, content)
        records.extend(emitted["nodes"])
        records.extend(emitted["edges"])
        diagnostics.extend(emitted["diagnostics"])
        if len(records) >= config.max_graph_records:
            diagnostics.append({"code": "semantic_graph_record_limit_reached", "path": rel})
            break
    records.extend(EquivalenceRuleRegistry().records())
    summary = {
        "enabled": True,
        "analyzed_files": analyzed_files,
        "recognized_languages": ["java"] if analyzed_files else [],
        "record_count": len(records),
        "node_count": sum(1 for row in records if (row.get("_provenance") or {}).get("output_kind") == "semantic_nodes"),
        "edge_count": sum(1 for row in records if (row.get("_provenance") or {}).get("output_kind") == "semantic_edges"),
        "rule_count": sum(1 for row in records if (row.get("_provenance") or {}).get("output_kind") == "equivalence_rules"),
        "warnings": [str(row.get("code") or row) for row in diagnostics],
    }
    return records, summary


def _login(hub: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    token = str((data.get("data") or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("Login failed — no access_token in response")
    return token


def _post_index(hub: str, token: str, records: list[dict], source_id: str) -> dict:
    payload = json.dumps({
        "source_scope": "artifact",
        "source_id": source_id,
        "records": records,
        "async": False,
        "profile_name": "deep_code",
        "source_metadata": {"project": "ananta", "indexed_by": "setup_codecompass_index.py"},
    }).encode()
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/knowledge/sources/index-records",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Index Ananta codebase into CodeCompass")
    parser.add_argument("--hub", default=os.environ.get("ANANTA_HUB_URL", "http://localhost:5000"))
    parser.add_argument("--user", default=os.environ.get("INITIAL_ADMIN_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("INITIAL_ADMIN_PASSWORD", "test123"))
    parser.add_argument("--source-id", default="ananta-project")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be indexed, don't POST")
    args = parser.parse_args()

    print(f"Scanning {ROOT} for source files…")
    files = _collect_files()
    records = _build_records(files)
    semantic_records, semantic_summary = _build_semantic_translation_records(files)
    if semantic_records:
        records.extend(semantic_records)
    print(f"  {len(files)} files found → {len(records)} indexable records")

    if semantic_summary.get("enabled"):
        print(
            "  Semantic Translation: "
            f"{semantic_summary['analyzed_files']} files, "
            f"{semantic_summary['node_count']} nodes, "
            f"{semantic_summary['edge_count']} edges, "
            f"{semantic_summary['rule_count']} rules"
        )

    if args.dry_run:
        for r in records[:20]:
            print(f"  {r['file']} ({len(r['content'])} chars)")
        if len(records) > 20:
            print(f"  … and {len(records) - 20} more")
        return 0

    print(f"Logging in to {args.hub}…")
    token = _login(args.hub, args.user, args.password)
    print("  OK")

    print(f"Posting {len(records)} records to /knowledge/sources/index-records…")
    result = _post_index(args.hub, token, records, args.source_id)
    status = result.get("status", "unknown")
    print(f"  Status: {status}")
    if status == "success":
        idx = (result.get("data") or {}).get("knowledge_index") or {}
        run = (result.get("data") or {}).get("run") or {}
        print(f"  Knowledge index ID : {idx.get('id', '?')}")
        print(f"  Run ID             : {run.get('id', '?')}")
        print(f"  Records indexed    : {(run.get('run_metadata') or {}).get('record_count', '?')}")
        print("\nDone. The snake chat will now have real Ananta project context.")
        return 0
    else:
        print(f"  ERROR: {result}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
