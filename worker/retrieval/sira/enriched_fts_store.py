from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worker.retrieval.sira.contracts import CompiledQuery, CorpusBinding, CorpusTermStat

_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ_][A-Za-zÀ-ÖØ-öø-ÿ0-9_]{1,79}")
_FIELDS = ("symbol_text", "path_text", "original_text", "enrichment_text", "relation_text")


class EnrichedFtsStore:
    """Snapshot-bound SQLite FTS5 store with precomputed corpus statistics."""

    def __init__(self, *, db_path: str | Path):
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._db_path))
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sira_metadata (
              row_id INTEGER PRIMARY KEY,
              record_id TEXT NOT NULL,
              file TEXT NOT NULL,
              kind TEXT NOT NULL,
              document_hash TEXT NOT NULL,
              source_manifest_hash TEXT NOT NULL,
              index_digest TEXT NOT NULL,
              statistics_digest TEXT NOT NULL,
              scope_key TEXT NOT NULL,
              source_id TEXT NOT NULL,
              source_version TEXT NOT NULL,
              tenant_id TEXT NOT NULL,
              source_scope TEXT NOT NULL,
              provenance_json TEXT NOT NULL,
              fields_json TEXT NOT NULL,
              UNIQUE(scope_key, record_id)
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS sira_fts USING fts5(
              symbol_text,
              path_text,
              original_text,
              enrichment_text,
              relation_text,
              content='',
              tokenize='unicode61'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sira_term_stats (
              scope_key TEXT NOT NULL,
              index_digest TEXT NOT NULL,
              statistics_digest TEXT NOT NULL,
              term TEXT NOT NULL,
              document_frequency INTEGER NOT NULL,
              collection_frequency INTEGER NOT NULL,
              document_count INTEGER NOT NULL,
              fields_json TEXT NOT NULL,
              PRIMARY KEY(scope_key, index_digest, term)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sira_active_snapshot (
              scope_key TEXT PRIMARY KEY,
              binding_json TEXT NOT NULL
            )
            """
        )

    def diagnostics(self, *, binding: CorpusBinding | None = None) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                if binding is None:
                    return {"status": "ready", "reason": "sqlite_fts5_available"}
                row = connection.execute(
                    "SELECT binding_json FROM sira_active_snapshot WHERE scope_key = ?",
                    (binding.scope_key,),
                ).fetchone()
                if row is None:
                    return {"status": "degraded", "reason": "sira_index_missing"}
                active = json.loads(str(row[0]))
                reason = self._binding_mismatch(active, binding)
                return {
                    "status": "ready" if not reason else "degraded",
                    "reason": reason or "sira_index_current",
                    "binding": active,
                }
        except sqlite3.DatabaseError:
            return {"status": "degraded", "reason": "sqlite_fts5_unavailable"}

    def rebuild(
        self,
        *,
        documents: Sequence[Mapping[str, Any]],
        enrichments: Mapping[str, Mapping[str, Any]],
        binding: CorpusBinding,
    ) -> dict[str, Any]:
        prepared: list[dict[str, Any]] = []
        document_frequency: Counter[str] = Counter()
        collection_frequency: Counter[str] = Counter()
        term_fields: dict[str, set[str]] = defaultdict(set)
        for raw in documents:
            document = dict(raw)
            record_id = str(document.get("record_id") or "").strip()
            if not record_id:
                continue
            text_fields = dict(document.get("text_fields") or {})
            enrichment = dict(enrichments.get(record_id) or {})
            generated = [
                str(item.get("value") or "").strip()
                for item in list(enrichment.get("generated_terms") or [])
                if isinstance(item, Mapping) and str(item.get("value") or "").strip()
            ]
            fields = {
                "symbol_text": str(text_fields.get("symbol_text") or ""),
                "path_text": str(text_fields.get("path_text") or document.get("file") or ""),
                "original_text": " ".join(
                    str(text_fields.get(key) or "") for key in ("summary_text", "content_text", "focus_text")
                ),
                "enrichment_text": " ".join(generated),
                "relation_text": str(text_fields.get("relation_text") or ""),
            }
            seen_in_document: set[str] = set()
            for field_name, field_text in fields.items():
                tokens = [value.casefold() for value in _TOKEN.findall(field_text)]
                field_terms = list(tokens)
                for size in range(2, min(4, len(tokens)) + 1):
                    field_terms.extend(
                        " ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)
                    )
                for term in field_terms:
                    collection_frequency[term] += 1
                    term_fields[term].add(field_name)
                    seen_in_document.add(term)
            document_frequency.update(seen_in_document)
            prepared.append({"document": document, "fields": fields})

        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            existing_rows = connection.execute(
                "SELECT row_id FROM sira_metadata WHERE scope_key = ?",
                (binding.scope_key,),
            ).fetchall()
            for row in existing_rows:
                connection.execute("DELETE FROM sira_fts WHERE rowid = ?", (int(row[0]),))
            connection.execute("DELETE FROM sira_metadata WHERE scope_key = ?", (binding.scope_key,))
            connection.execute("DELETE FROM sira_term_stats WHERE scope_key = ?", (binding.scope_key,))
            for item in prepared:
                document = item["document"]
                fields = item["fields"]
                provenance = dict(document.get("provenance") or {})
                cursor = connection.execute(
                    """
                    INSERT INTO sira_metadata (
                      record_id, file, kind, document_hash, source_manifest_hash, index_digest,
                      statistics_digest, scope_key, source_id, source_version, tenant_id,
                      source_scope, provenance_json, fields_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(document.get("record_id") or ""),
                        str(document.get("file") or ""),
                        str(document.get("kind") or "unknown"),
                        str(document.get("document_hash") or ""),
                        binding.source_manifest_hash,
                        binding.index_digest,
                        binding.statistics_digest,
                        binding.scope_key,
                        str(document.get("source_id") or ""),
                        str(document.get("source_version") or binding.repository_revision),
                        str(document.get("tenant_id") or binding.tenant_id),
                        str(document.get("scope") or binding.scope),
                        json.dumps(provenance, sort_keys=True, separators=(",", ":")),
                        json.dumps(fields, sort_keys=True, separators=(",", ":")),
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("sira_metadata_row_id_missing")
                connection.execute(
                    "INSERT INTO sira_fts("
                    "rowid, symbol_text, path_text, original_text, enrichment_text, relation_text"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (int(cursor.lastrowid), *(fields[field] for field in _FIELDS)),
                )
            document_count = len(prepared)
            for term in sorted(collection_frequency):
                connection.execute(
                    "INSERT INTO sira_term_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        binding.scope_key,
                        binding.index_digest,
                        binding.statistics_digest,
                        term,
                        int(document_frequency[term]),
                        int(collection_frequency[term]),
                        document_count,
                        json.dumps(sorted(term_fields[term]), separators=(",", ":")),
                    ),
                )
            connection.execute(
                "INSERT INTO sira_active_snapshot(scope_key, binding_json) VALUES (?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET binding_json = excluded.binding_json",
                (binding.scope_key, json.dumps(binding.to_dict(), sort_keys=True, separators=(",", ":"))),
            )
            connection.commit()
        return {
            "status": "ok",
            "indexed_documents": len(prepared),
            "indexed_terms": len(collection_frequency),
            "binding": binding.to_dict(),
        }

    def lookup(self, terms: Sequence[str], *, binding: CorpusBinding) -> Mapping[str, CorpusTermStat]:
        normalized = sorted(
            {" ".join(_TOKEN.findall(str(term))).casefold() for term in terms if _TOKEN.findall(str(term))}
        )
        if not normalized:
            return {}
        self._require_binding(binding)
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT term, document_frequency, collection_frequency, document_count, fields_json "
                f"FROM sira_term_stats WHERE scope_key = ? AND index_digest = ? AND term IN ({placeholders})",
                (binding.scope_key, binding.index_digest, *normalized),
            ).fetchall()
        return {
            str(row["term"]): CorpusTermStat(
                term=str(row["term"]),
                document_frequency=int(row["document_frequency"]),
                collection_frequency=int(row["collection_frequency"]),
                document_count=int(row["document_count"]),
                fields=tuple(json.loads(str(row["fields_json"]))),
            )
            for row in rows
        }

    def search_weighted(self, query: CompiledQuery, *, top_k: int) -> list[dict[str, Any]]:
        self._require_binding(query.binding)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.*, bm25(sira_fts, 8.0, 5.0, 3.0, 1.5, 2.0) AS bm25_score
                FROM sira_fts
                JOIN sira_metadata m ON m.row_id = sira_fts.rowid
                WHERE m.scope_key = ? AND m.index_digest = ? AND sira_fts MATCH ?
                ORDER BY bm25_score, m.record_id
                LIMIT ?
                """,
                (
                    query.binding.scope_key,
                    query.binding.index_digest,
                    query.match_expression,
                    max(1, int(top_k) * 4),
                ),
            ).fetchall()
        candidates: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            fields = json.loads(str(row["fields_json"]))
            haystack = " ".join(str(fields.get(name) or "") for name in _FIELDS).casefold()
            contributions = {term.value: term.weight for term in query.terms if term.value.casefold() in haystack}
            weighted_score = sum(contributions.values())
            lexical_rank_score = 1.0 / float(rank)
            score = lexical_rank_score + weighted_score
            candidates.append(
                {
                    "record_id": str(row["record_id"]),
                    "path": str(row["file"]),
                    "content": str(fields.get("original_text") or "")[:4_000],
                    "score": score,
                    "source_id": str(row["source_id"]),
                    "source_version": str(row["source_version"]),
                    "tenant_id": str(row["tenant_id"]),
                    "scope": str(row["source_scope"]),
                    "provenance": json.loads(str(row["provenance_json"])),
                    "metadata": {
                        "record_id": str(row["record_id"]),
                        "record_kind": str(row["kind"]),
                        "file": str(row["file"]),
                        "document_hash": str(row["document_hash"]),
                        "source_manifest_hash": str(row["source_manifest_hash"]),
                        "index_digest": str(row["index_digest"]),
                        "statistics_digest": str(row["statistics_digest"]),
                        "bm25_score": max(0.0, -float(row["bm25_score"] or 0.0)),
                        "lexical_rank_score": lexical_rank_score,
                        "sira_term_contributions": contributions,
                        "sira_weighted_score": weighted_score,
                        "sira_ranking_version": query.ranking_version,
                        "retrieval_profile": "corpus_discriminative_lexical",
                    },
                }
            )
        return sorted(candidates, key=lambda item: (-float(item["score"]), str(item["record_id"])))[: max(1, top_k)]

    def _require_binding(self, binding: CorpusBinding) -> None:
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                "SELECT binding_json FROM sira_active_snapshot WHERE scope_key = ?",
                (binding.scope_key,),
            ).fetchone()
        if row is None:
            raise ValueError("sira_index_missing")
        active = json.loads(str(row[0]))
        mismatch = self._binding_mismatch(active, binding)
        if mismatch:
            raise ValueError(mismatch)

    @staticmethod
    def _binding_mismatch(active: Mapping[str, Any], expected: CorpusBinding) -> str:
        for field, reason in (
            ("source_manifest_hash", "sira_manifest_mismatch"),
            ("index_digest", "sira_index_mismatch"),
            ("statistics_digest", "sira_statistics_mismatch"),
            ("profile_version", "sira_profile_mismatch"),
            ("repository_revision", "sira_revision_mismatch"),
            ("tenant_id", "sira_tenant_mismatch"),
            ("scope", "sira_scope_mismatch"),
        ):
            if str(active.get(field) or "") != str(getattr(expected, field)):
                return reason
        return ""


def derive_index_digests(
    *,
    documents: Sequence[Mapping[str, Any]],
    prompt_version: str,
    model_digest: str,
    schema_version: str = "codecompass.sira-enrichment.v1",
) -> tuple[str, str]:
    document_hashes = sorted(str(document.get("document_hash") or "") for document in documents)
    seed = json.dumps(
        {
            "document_hashes": document_hashes,
            "prompt_version": prompt_version,
            "model_digest": model_digest,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    index_digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    statistics_digest = hashlib.sha256(f"stats:{index_digest}".encode("utf-8")).hexdigest()
    return index_digest, statistics_digest
