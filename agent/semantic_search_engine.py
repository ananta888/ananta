from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from agent.repository_map_engine import ContextChunk
from agent.hybrid_context_support import (
    build_file_manifest,
    manifest_needs_reingest,
    read_manifest,
    write_manifest,
)

try:
    from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage
    from llama_index.core.readers import SimpleDirectoryReader
except Exception:  # pragma: no cover - optional dependency
    StorageContext = None
    VectorStoreIndex = None
    load_index_from_storage = None
    SimpleDirectoryReader = None


class SemanticSearchEngine:
    """LlamaIndex retrieval with persistent index and ingestion manifest."""

    TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".pdf"}
    # .jsonl and .log are internal data files, not semantic documentation
    _FALLBACK_STOP_TOKENS: frozenset = frozenset({
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
        "mir", "dir", "ihm", "ihr", "uns", "ich", "du", "er", "sie", "wir",
        "und", "oder", "aber", "nicht", "auch", "noch", "von", "mit", "bei",
        "aus", "zur", "zum", "ist", "sind", "war", "wird", "hat", "haben",
        "auf", "in", "an", "zu", "am", "im", "als", "bitte", "mal",
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "has", "its", "was", "use", "one", "how", "our", "out", "that",
        "this", "with", "from", "have", "will", "been", "they", "their",
    })

    def __init__(
        self,
        data_roots: list[str | Path],
        persist_dir: str | Path,
        max_total_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.data_roots = [Path(p).resolve() for p in data_roots]
        self.persist_dir = Path(persist_dir).resolve()
        self.max_total_bytes = max_total_bytes
        self._index = None
        self._fallback_docs: list[tuple[str, str]] = []
        self._built = False
        self._manifest_path = self.persist_dir / "manifest.json"

    def _iter_candidate_files(self) -> list[Path]:
        total = 0
        files: list[Path] = []
        for root in self.data_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in self.TEXT_EXTENSIONS:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > 15 * 1024 * 1024:
                    continue
                if total + size > self.max_total_bytes:
                    return files
                total += size
                files.append(path)
        return files

    def _build_manifest(self, files: list[Path]) -> dict[str, object]:
        return build_file_manifest(files)

    def _read_manifest(self) -> dict[str, object]:
        return read_manifest(self._manifest_path)

    def _write_manifest(self, manifest: dict[str, object]) -> None:
        write_manifest(self._manifest_path, manifest)

    def _needs_reingest(self, files: list[Path]) -> bool:
        return manifest_needs_reingest(files=files, manifest_path=self._manifest_path)

    def _load_or_build_index(self, files: list[Path]) -> None:
        if str(os.environ.get("ANANTA_ENABLE_LLAMAINDEX_EMBEDDINGS") or "").strip().lower() not in {"1", "true", "yes"}:
            # Default to local/fallback retrieval unless embeddings are explicitly enabled.
            self._index = None
            return
        if (
            VectorStoreIndex is None
            or StorageContext is None
            or load_index_from_storage is None
            or SimpleDirectoryReader is None
            or not files
        ):
            return

        needs_reingest = self._needs_reingest(files)
        if not needs_reingest and self.persist_dir.exists():
            try:
                storage = StorageContext.from_defaults(persist_dir=str(self.persist_dir))
                self._index = load_index_from_storage(storage)
                return
            except Exception as e:
                logging.warning(f"Failed loading persisted semantic index from '{self.persist_dir}': {e}")
                self._index = None

        try:
            reader = SimpleDirectoryReader(input_files=[str(f) for f in files])
            docs = reader.load_data()
            self._index = VectorStoreIndex.from_documents(docs, show_progress=False)
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._index.storage_context.persist(persist_dir=str(self.persist_dir))
            self._write_manifest(self._build_manifest(files))
        except Exception as e:
            logging.warning(f"Failed building semantic index: {e}")
            self._index = None

    def build(self) -> None:
        if self._built:
            return
        files = self._iter_candidate_files()
        self._load_or_build_index(files)
        if self._index is None:
            for file_path in files:
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception as e:
                    logging.debug(f"Failed reading fallback semantic file '{file_path}': {e}")
                    continue
                if text:
                    self._fallback_docs.append((str(file_path), text[:12000]))
        self._built = True

    def search(self, query: str, top_k: int = 4) -> list[ContextChunk]:
        self.build()
        if self._index is not None:
            try:
                retriever = self._index.as_retriever(similarity_top_k=top_k)
                nodes = retriever.retrieve(query)
                chunks: list[ContextChunk] = []
                for node in nodes:
                    text = getattr(node, "text", "") or getattr(node.node, "text", "")
                    score = float(getattr(node, "score", 0.0) or 0.0)
                    metadata = getattr(node, "metadata", {}) or {}
                    source = str(metadata.get("file_path", "llamaindex"))
                    chunks.append(
                        ContextChunk(
                            engine="semantic_search",
                            source=source,
                            content=text[:2000],
                            score=score,
                        )
                    )
                return chunks
            except Exception as e:
                logging.warning(f"LlamaIndex semantic search failed for query '{query[:50]}...': {e}")

        tokens = [
            t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query)
            if len(t) >= 3 and t.lower() not in self._FALLBACK_STOP_TOKENS
        ]
        fallback: list[ContextChunk] = []
        for source, text in self._fallback_docs:
            lower = text.lower()
            score = sum(lower.count(token) for token in tokens)
            if score <= 0:
                continue
            fallback.append(
                ContextChunk(
                    engine="semantic_search",
                    source=source,
                    content=text[:2000],
                    score=float(score),
                )
            )
        return sorted(fallback, key=lambda c: c.score, reverse=True)[:top_k]
