from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from git import Repo
except Exception:  # pragma: no cover - optional dependency
    Repo = None

try:
    from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage
    from llama_index.core.readers import SimpleDirectoryReader
except Exception:  # pragma: no cover - optional dependency
    StorageContext = None
    VectorStoreIndex = None
    load_index_from_storage = None
    SimpleDirectoryReader = None

try:
    from tree_sitter import Parser
except Exception:  # pragma: no cover - optional dependency
    Parser = None

try:
    from tree_sitter_languages import get_parser
except Exception:  # pragma: no cover - optional dependency
    get_parser = None

from agent.common.sgpt import run_sgpt_command


@dataclass(slots=True)
class ContextChunk:
    engine: str
    source: str
    content: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


class RepositoryMapEngine:
    """Aider-style repository symbol map with incremental cache invalidation."""

    CODE_EXTENSIONS = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
    }
    TREE_SITTER_LANGUAGE_BY_EXT = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
    }
    TREE_SITTER_SYMBOL_NODES = {
        "class_definition",
        "function_definition",
        "function_declaration",
        "method_definition",
        "interface_declaration",
        "type_declaration",
        "enum_declaration",
        "struct_item",
        "impl_item",
    }

    def __init__(
        self,
        repo_root: str | Path,
        max_files: int = 4000,
        max_symbols_per_file: int = 80,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_files = max_files
        self.max_symbols_per_file = max_symbols_per_file
        self._symbol_graph: dict[str, list[str]] = {}
        self._file_state: dict[str, tuple[float, int]] = {}
        self._last_scan_ts = 0.0

    def _tracked_files(self) -> list[Path]:
        if Repo is not None:
            try:
                repo = Repo(self.repo_root, search_parent_directories=True)
                root = Path(repo.working_tree_dir or self.repo_root)
                files = [
                    (root / rel).resolve()
                    for rel in repo.git.ls_files().splitlines()
                    if Path(rel).suffix.lower() in self.CODE_EXTENSIONS
                ]
                return files[: self.max_files]
            except Exception:
                pass

        files: list[Path] = []
        for current_root, dirs, file_names in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache"}]
            for name in file_names:
                path = Path(current_root) / name
                if path.suffix.lower() in self.CODE_EXTENSIONS:
                    files.append(path.resolve())
                    if len(files) >= self.max_files:
                        return files
        return files

    def _parser_for_file(self, file_path: Path):
        if Parser is None or get_parser is None:
            return None
        lang = self.TREE_SITTER_LANGUAGE_BY_EXT.get(file_path.suffix.lower())
        if not lang:
            return None
        try:
            return get_parser(lang)
        except Exception:
            return None

    @staticmethod
    def _decode_node_text(node, source: bytes) -> str:
        try:
            return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_symbols_tree_sitter(self, file_path: Path, text: str) -> list[str]:
        parser = self._parser_for_file(file_path)
        if parser is None:
            return []
        source = text.encode("utf-8", errors="ignore")
        try:
            tree = parser.parse(source)
        except Exception:
            return []

        symbols: list[str] = []

        def visit(node, parents: list[str]) -> None:
            if len(symbols) >= self.max_symbols_per_file:
                return
            current_parents = parents
            if node.type in self.TREE_SITTER_SYMBOL_NODES:
                name = ""
                for child in node.children:
                    if child.type in {"name", "identifier", "property_identifier", "type_identifier"}:
                        name = self._decode_node_text(child, source).strip()
                        break
                if name:
                    qualified = ".".join([*parents, name]) if parents else name
                    symbols.append(qualified)
                    current_parents = [*parents, name]
            for child in node.children:
                visit(child, current_parents)

        visit(tree.root_node, [])
        return symbols

    def _extract_symbols_regex(self, text: str) -> list[str]:
        patterns = [
            r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:(]",
            r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\(",
            r"^\s*interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*[{<]",
        ]
        symbols: list[str] = []
        for line in text.splitlines():
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    symbols.append(match.group(1))
                    break
            if len(symbols) >= self.max_symbols_per_file:
                break
        return symbols

    def build(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_scan_ts < 1.0):
            return
        self._last_scan_ts = now

        active: set[str] = set()
        for file_path in self._tracked_files():
            rel = str(file_path.relative_to(self.repo_root))
            active.add(rel)
            try:
                stat = file_path.stat()
            except Exception:
                continue
            state = (stat.st_mtime, stat.st_size)
            if not force and self._file_state.get(rel) == state:
                continue
            self._file_state[rel] = state
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                self._symbol_graph.pop(rel, None)
                continue
            symbols = self._extract_symbols_tree_sitter(file_path, text) or self._extract_symbols_regex(text)
            if symbols:
                self._symbol_graph[rel] = symbols
            else:
                self._symbol_graph.pop(rel, None)

        removed = set(self._file_state.keys()) - active
        for rel in removed:
            self._file_state.pop(rel, None)
            self._symbol_graph.pop(rel, None)

    def search(self, query: str, top_k: int = 5) -> list[ContextChunk]:
        self.build()
        if not self._symbol_graph:
            return []
        tokens = {t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2}
        candidates: list[ContextChunk] = []
        for rel_path, symbols in self._symbol_graph.items():
            score = 0.0
            path_lower = rel_path.lower()
            sym_lower = [s.lower() for s in symbols]
            for token in tokens:
                if token in path_lower:
                    score += 1.4
                score += sum(1.0 for sym in sym_lower if token in sym)
            if score <= 0:
                continue
            preview = ", ".join(symbols[:20])
            candidates.append(
                ContextChunk(
                    engine="repository_map",
                    source=rel_path,
                    content=f"{rel_path}\nSymbols: {preview}",
                    score=score,
                    metadata={"symbol_count": str(len(symbols))},
                )
            )
        return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]


@dataclass(slots=True)
class SearchSkill:
    name: str
    priority: int
    trigger: Callable[[str], bool]
    build_command: Callable[[str], list[str]]


class AgenticSearchEngine:
    """Vibe-like skill registry with deterministic planning and execution budgets."""

    METACHAR_PATTERN = re.compile(r"[;&|`><$(){}]")

    def __init__(
        self,
        repo_root: str | Path,
        max_output_chars: int = 5000,
        max_commands: int = 3,
        command_timeout_seconds: int = 8,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_output_chars = max_output_chars
        self.max_commands = max_commands
        self.command_timeout_seconds = command_timeout_seconds
        self.allowed_commands = {"rg", "ls", "cat"}
        self.skills = [
            SearchSkill(
                name="file_discovery",
                priority=1,
                trigger=lambda q: any(w in q.lower() for w in ("where", "datei", "file", "struktur", "tree")),
                build_command=lambda _q: ["rg", "--files"],
            ),
            SearchSkill(
                name="config_probe",
                priority=2,
                trigger=lambda q: any(w in q.lower() for w in ("config", "env", "setting", "einstellung")),
                build_command=lambda _q: ["rg", "--files", "-g", "*.env", "-g", "*.json", "-g", "*.yaml", "-g", "*.yml"],
            ),
            SearchSkill(
                name="text_grep",
                priority=3,
                trigger=lambda _q: True,
                build_command=lambda q: ["rg", "-n", "--no-heading", "--max-count", "40", self._sanitize_query(q), "."],
            ),
        ]

    @classmethod
    def _sanitize_query(cls, query: str) -> str:
        cleaned = re.sub(r"[\r\n\t]+", " ", query).strip()
        if cls.METACHAR_PATTERN.search(cleaned):
            cleaned = cls.METACHAR_PATTERN.sub(" ", cleaned)
        return cleaned[:180]

    def _is_allowed_command(self, args: list[str]) -> bool:
        if not args or args[0] not in self.allowed_commands:
            return False
        return all("\n" not in arg and "\r" not in arg for arg in args)

    def _run(self, args: list[str]) -> str:
        if not self._is_allowed_command(args):
            return ""
        try:
            completed = subprocess.run(
                args,
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                shell=False,
            )
        except Exception:
            return ""
        output = completed.stdout.strip() or completed.stderr.strip()
        return output[: self.max_output_chars]

    def _plan(self, query: str) -> list[SearchSkill]:
        matches = [skill for skill in self.skills if skill.trigger(query)]
        matches.sort(key=lambda skill: skill.priority)
        return matches[: self.max_commands]

    def search(self, query: str, top_k: int = 3) -> list[ContextChunk]:
        planned = self._plan(query)
        max_steps = min(len(planned), self.max_commands, max(top_k, 1))
        chunks: list[ContextChunk] = []
        used_output = 0
        for skill in planned[:max_steps]:
            args = skill.build_command(query)
            out = self._run(args)
            if not out:
                continue
            remaining = self.max_output_chars - used_output
            if remaining <= 0:
                break
            out = out[:remaining]
            used_output += len(out)
            chunks.append(
                ContextChunk(
                    engine="agentic_search",
                    source=" ".join(args),
                    content=out,
                    score=1.0 + min(len(out) / 5000.0, 1.0),
                    metadata={"skill": skill.name},
                )
            )
        return chunks[:top_k]


class SemanticSearchEngine:
    """LlamaIndex retrieval with persistent index and ingestion manifest."""

    TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".pdf", ".log", ".jsonl"}

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
        entries: dict[str, dict[str, object]] = {}
        digest = hashlib.sha256()
        for path in sorted(files):
            rel = str(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            entries[rel] = {"mtime": stat.st_mtime, "size": stat.st_size}
            digest.update(f"{rel}|{stat.st_mtime}|{stat.st_size}".encode("utf-8", errors="ignore"))
        return {"files": entries, "fingerprint": digest.hexdigest()}

    def _read_manifest(self) -> dict[str, object]:
        if not self._manifest_path.exists():
            return {}
        try:
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_manifest(self, manifest: dict[str, object]) -> None:
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _needs_reingest(self, files: list[Path]) -> bool:
        current = self._build_manifest(files)
        existing = self._read_manifest()
        return not existing or existing.get("fingerprint") != current.get("fingerprint")

    def _load_or_build_index(self, files: list[Path]) -> None:
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
            except Exception:
                self._index = None

        try:
            reader = SimpleDirectoryReader(input_files=[str(f) for f in files])
            docs = reader.load_data()
            self._index = VectorStoreIndex.from_documents(docs, show_progress=False)
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._index.storage_context.persist(persist_dir=str(self.persist_dir))
            self._write_manifest(self._build_manifest(files))
        except Exception:
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
                except Exception:
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
            except Exception:
                pass

        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2]
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


class ContextManager:
    """Versioned decision policy for engine routing and diversity-aware reranking."""

    def __init__(self, policy_version: str = "v1") -> None:
        self.policy_version = policy_version

    def route(self, query: str) -> dict[str, int]:
        q = query.lower()
        code_like = any(k in q for k in ("class", "function", "bug", "stacktrace", "repo", "module", "python", ".py"))
        docs_like = any(k in q for k in ("pdf", "doku", "documentation", "log", "readme", "spec"))
        fs_like = any(k in q for k in ("find", "suche", "where", "ls", "grep", "datei", "folder"))

        quotas = {"repository_map": 0, "semantic_search": 0, "agentic_search": 0}
        if code_like:
            quotas["repository_map"] += 4
            quotas["agentic_search"] += 1
        if docs_like:
            quotas["semantic_search"] += 4
            quotas["repository_map"] += 1
        if fs_like:
            quotas["agentic_search"] += 3
            quotas["repository_map"] += 1
        if all(v == 0 for v in quotas.values()):
            quotas = {"repository_map": 2, "semantic_search": 2, "agentic_search": 1}
        return quotas

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def rerank(
        self,
        chunks: list[ContextChunk],
        query: str,
        max_chunks: int,
        max_chars: int,
        max_tokens: int,
    ) -> list[ContextChunk]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2]
        for chunk in chunks:
            text = f"{chunk.source}\n{chunk.content}".lower()
            lexical = sum(text.count(token) for token in tokens)
            chunk.score = float(chunk.score) + lexical * 0.25

        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        engine_heads: dict[str, ContextChunk] = {}
        for chunk in ranked:
            engine_heads.setdefault(chunk.engine, chunk)

        selected: list[ContextChunk] = []
        used = set()
        chars = 0
        token_budget = 0

        # diversity-first: keep strongest candidate per engine where possible
        for chunk in engine_heads.values():
            key = (chunk.engine, chunk.source, chunk.content[:120])
            c_tokens = self.estimate_tokens(chunk.content)
            if key in used:
                continue
            if chars + len(chunk.content) > max_chars or token_budget + c_tokens > max_tokens:
                continue
            selected.append(chunk)
            used.add(key)
            chars += len(chunk.content)
            token_budget += c_tokens
            if len(selected) >= max_chunks:
                return selected

        for chunk in ranked:
            key = (chunk.engine, chunk.source, chunk.content[:120])
            c_tokens = self.estimate_tokens(chunk.content)
            if key in used:
                continue
            if len(selected) >= max_chunks:
                break
            if chars + len(chunk.content) > max_chars or token_budget + c_tokens > max_tokens:
                continue
            selected.append(chunk)
            used.add(key)
            chars += len(chunk.content)
            token_budget += c_tokens
        return selected


class HybridOrchestrator:
    """Central orchestrator for repository-map, semantic retrieval and agentic search."""

    SECRET_PATTERNS = [
        re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        re.compile(r"\b([A-Za-z0-9_]*(?:token|password|secret|apikey|api_key)[A-Za-z0-9_]*\s*[:=]\s*['\"]?[^'\"\s]{6,})", re.I),
        re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"),
    ]

    def __init__(
        self,
        repo_root: str | Path,
        data_roots: list[str | Path] | None = None,
        max_context_chars: int = 12000,
        max_context_tokens: int = 3000,
        max_chunks: int = 12,
        agentic_max_commands: int = 3,
        agentic_timeout_seconds: int = 8,
        semantic_persist_dir: str | Path | None = None,
        redact_sensitive: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.data_roots = data_roots or [self.repo_root / "docs", self.repo_root / "data"]
        self.max_context_chars = max_context_chars
        self.max_context_tokens = max_context_tokens
        self.max_chunks = max_chunks
        self.redact_sensitive = redact_sensitive

        persist_dir = Path(semantic_persist_dir) if semantic_persist_dir else (self.repo_root / ".rag" / "llamaindex")
        self.repository_engine = RepositoryMapEngine(self.repo_root)
        self.agentic_engine = AgenticSearchEngine(
            self.repo_root,
            max_commands=agentic_max_commands,
            command_timeout_seconds=agentic_timeout_seconds,
        )
        self.semantic_engine = SemanticSearchEngine(self.data_roots, persist_dir=persist_dir)
        self.context_manager = ContextManager(policy_version="v1")

    def _redact(self, text: str) -> str:
        if not self.redact_sensitive:
            return text
        redacted = text
        for pattern in self.SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

    def get_relevant_context(self, query: str) -> dict[str, object]:
        quotas = self.context_manager.route(query)
        chunks: list[ContextChunk] = []

        if quotas["repository_map"] > 0:
            chunks.extend(self.repository_engine.search(query, top_k=quotas["repository_map"]))
        if quotas["semantic_search"] > 0:
            chunks.extend(self.semantic_engine.search(query, top_k=quotas["semantic_search"]))
        if quotas["agentic_search"] > 0:
            chunks.extend(self.agentic_engine.search(query, top_k=quotas["agentic_search"]))

        best = self.context_manager.rerank(
            chunks=chunks,
            query=query,
            max_chunks=self.max_chunks,
            max_chars=self.max_context_chars,
            max_tokens=self.max_context_tokens,
        )

        serialized_chunks = []
        context_lines: list[str] = []
        for chunk in best:
            safe_content = self._redact(chunk.content)
            context_lines.append(f"[{chunk.engine}] {chunk.source}\n{safe_content}")
            serialized_chunks.append(
                {
                    "engine": chunk.engine,
                    "source": chunk.source,
                    "score": round(chunk.score, 3),
                    "content": safe_content,
                    "metadata": chunk.metadata,
                }
            )

        context_text = "\n\n".join(context_lines)
        return {
            "query": query,
            "strategy": quotas,
            "policy_version": self.context_manager.policy_version,
            "chunks": serialized_chunks,
            "context_text": context_text,
            "token_estimate": self.context_manager.estimate_tokens(context_text),
        }

    def run_with_sgpt(self, query: str, options: list[str] | None = None) -> dict[str, object]:
        context = self.get_relevant_context(query)
        prompt = (
            "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\n"
            f"Frage:\n{query}\n\n"
            f"Kontext:\n{context['context_text']}"
        )
        rc, output, errors = run_sgpt_command(prompt=prompt, options=options or ["--no-interaction"])
        return {
            "returncode": rc,
            "output": output,
            "errors": errors,
            "context": context,
        }
