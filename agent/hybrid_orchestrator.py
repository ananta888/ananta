from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from git import Repo
except Exception:  # pragma: no cover - optional dependency
    Repo = None

try:
    from llama_index.core import VectorStoreIndex
    from llama_index.core.readers import SimpleDirectoryReader
except Exception:  # pragma: no cover - optional dependency
    VectorStoreIndex = None
    SimpleDirectoryReader = None

try:
    from tree_sitter import Parser
except Exception:  # pragma: no cover - optional dependency
    Parser = None

try:
    from tree_sitter_languages import get_parser
except Exception:  # pragma: no cover - optional dependency
    get_parser = None

try:
    from mistralai import Mistral
except Exception:  # pragma: no cover - optional dependency
    Mistral = None

from agent.common.sgpt import run_sgpt_command


@dataclass(slots=True)
class ContextChunk:
    engine: str
    source: str
    content: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


class RepositoryMapEngine:
    """Aider-inspired repository map using lightweight symbol extraction."""

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
        self._built = False

    def _parser_for_file(self, file_path: Path):
        if get_parser is None or Parser is None:
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

    def _tracked_files(self) -> list[Path]:
        if Repo is not None:
            try:
                repo = Repo(self.repo_root, search_parent_directories=True)
                root = Path(repo.working_tree_dir or self.repo_root)
                return [
                    (root / rel).resolve()
                    for rel in repo.git.ls_files().splitlines()
                    if Path(rel).suffix.lower() in self.CODE_EXTENSIONS
                ][: self.max_files]
            except Exception:
                pass

        files: list[Path] = []
        for current_root, dirs, file_names in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "node_modules", "__pycache__"}]
            for name in file_names:
                path = Path(current_root) / name
                if path.suffix.lower() in self.CODE_EXTENSIONS:
                    files.append(path.resolve())
                    if len(files) >= self.max_files:
                        return files
        return files

    def _extract_symbols_regex(self, text: str) -> list[str]:
        patterns = [
            r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:(]",
            r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\(",
            r"^\s*interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*[{<]",
        ]
        symbols: list[str] = []
        lines = text.splitlines()
        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    symbols.append(match.group(1))
                    break
            if len(symbols) >= self.max_symbols_per_file:
                break
        return symbols

    def build(self) -> None:
        if self._built:
            return

        for file_path in self._tracked_files():
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            symbols = self._extract_symbols_tree_sitter(file_path, text) or self._extract_symbols_regex(text)
            rel_path = str(file_path.relative_to(self.repo_root))
            if symbols:
                self._symbol_graph[rel_path] = symbols
        self._built = True

    def search(self, query: str, top_k: int = 5) -> list[ContextChunk]:
        self.build()
        if not self._symbol_graph:
            return []
        tokens = {t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2}
        candidates: list[ContextChunk] = []
        for rel_path, symbols in self._symbol_graph.items():
            sym_lower = [s.lower() for s in symbols]
            path_lower = rel_path.lower()
            score = 0.0
            for token in tokens:
                if token in path_lower:
                    score += 1.4
                score += sum(1.0 for sym in sym_lower if token in sym)
            if score <= 0:
                continue
            preview = ", ".join(symbols[:20])
            content = f"{rel_path}\nSymbols: {preview}"
            candidates.append(
                ContextChunk(
                    engine="repository_map",
                    source=rel_path,
                    content=content,
                    score=score,
                    metadata={"symbol_count": str(len(symbols))},
                )
            )
        return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]


@dataclass(slots=True)
class SearchSkill:
    name: str
    trigger: Callable[[str], bool]
    build_command: Callable[[str], str]


class AgenticSearchEngine:
    """Vibe-like agentic shell search with skill routing and strict command allowlist."""

    def __init__(
        self,
        repo_root: str | Path,
        max_output_chars: int = 5000,
        mistral_api_key: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_output_chars = max_output_chars
        self.mistral_client = Mistral(api_key=mistral_api_key) if mistral_api_key and Mistral else None
        self.skills = [
            SearchSkill(
                name="file_discovery",
                trigger=lambda q: any(w in q.lower() for w in ("where", "datei", "file", "struktur", "tree")),
                build_command=lambda q: "rg --files",
            ),
            SearchSkill(
                name="text_grep",
                trigger=lambda q: True,
                build_command=lambda q: f"rg -n --no-heading --max-count 30 {shlex.quote(q)} .",
            ),
            SearchSkill(
                name="config_probe",
                trigger=lambda q: any(w in q.lower() for w in ("config", "env", "setting", "einstellung")),
                build_command=lambda q: "ls",
            ),
        ]

    def _is_allowed_command(self, command: str) -> bool:
        allow_prefixes = ("rg ", "ls", "dir", "cat ", "Get-Content ")
        return command.startswith(allow_prefixes)

    def _run(self, command: str) -> str:
        if not self._is_allowed_command(command):
            return ""
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=12,
            )
        except Exception:
            return ""
        output = completed.stdout.strip() or completed.stderr.strip()
        return output[: self.max_output_chars]

    def search(self, query: str, top_k: int = 3) -> list[ContextChunk]:
        hits: list[ContextChunk] = []
        for skill in self.skills:
            if not skill.trigger(query):
                continue
            command = skill.build_command(query)
            output = self._run(command)
            if not output:
                continue
            score = 1.0 + min(len(output) / 5000.0, 1.0)
            hits.append(
                ContextChunk(
                    engine="agentic_search",
                    source=command,
                    content=output,
                    score=score,
                    metadata={"skill": skill.name},
                )
            )
            if len(hits) >= top_k:
                break
        return hits


class SemanticSearchEngine:
    """LlamaIndex-based semantic retrieval for non-code text assets."""

    TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".pdf", ".log", ".jsonl"}

    def __init__(
        self,
        data_roots: list[str | Path],
        max_total_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.data_roots = [Path(p).resolve() for p in data_roots]
        self.max_total_bytes = max_total_bytes
        self._index = None
        self._fallback_docs: list[tuple[str, str]] = []
        self._built = False

    def _iter_candidate_files(self) -> list[Path]:
        total = 0
        files: list[Path] = []
        for root in self.data_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in self.TEXT_EXTENSIONS:
                    continue
                size = path.stat().st_size
                if size > 15 * 1024 * 1024:
                    continue
                if total + size > self.max_total_bytes:
                    return files
                total += size
                files.append(path)
        return files

    def build(self) -> None:
        if self._built:
            return
        files = self._iter_candidate_files()
        if VectorStoreIndex is not None and SimpleDirectoryReader is not None and files:
            try:
                reader = SimpleDirectoryReader(input_files=[str(f) for f in files])
                docs = reader.load_data()
                self._index = VectorStoreIndex.from_documents(docs, show_progress=False)
                self._built = True
                return
            except Exception:
                self._index = None
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
                    source = str(getattr(getattr(node, "metadata", {}), "get", lambda *_: "")("file_path", ""))
                    chunks.append(
                        ContextChunk(
                            engine="semantic_search",
                            source=source or "llamaindex",
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
    """Decision logic to pick and blend retrieval engines."""

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
    def rerank(chunks: list[ContextChunk], query: str, max_chunks: int, max_chars: int) -> list[ContextChunk]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2]
        for chunk in chunks:
            text = f"{chunk.source}\n{chunk.content}".lower()
            lexical = sum(text.count(token) for token in tokens)
            chunk.score = float(chunk.score) + lexical * 0.25
        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        selected: list[ContextChunk] = []
        chars = 0
        seen = set()
        for chunk in ranked:
            key = (chunk.engine, chunk.source, chunk.content[:120])
            if key in seen:
                continue
            seen.add(key)
            if len(selected) >= max_chunks:
                break
            if chars + len(chunk.content) > max_chars:
                continue
            selected.append(chunk)
            chars += len(chunk.content)
        return selected


class HybridOrchestrator:
    """
    Central RAG orchestrator for:
    - Engine A: repository map (Aider-style)
    - Engine B: agentic shell search (Vibe-style)
    - Engine C: semantic retrieval (LlamaIndex)
    """

    def __init__(
        self,
        repo_root: str | Path,
        data_roots: list[str | Path] | None = None,
        max_context_chars: int = 12000,
        mistral_api_key: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.data_roots = data_roots or [self.repo_root / "docs", self.repo_root / "data"]
        self.max_context_chars = max_context_chars

        self.repository_engine = RepositoryMapEngine(self.repo_root)
        self.agentic_engine = AgenticSearchEngine(self.repo_root, mistral_api_key=mistral_api_key)
        self.semantic_engine = SemanticSearchEngine(self.data_roots)
        self.context_manager = ContextManager()

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
            max_chunks=12,
            max_chars=self.max_context_chars,
        )

        context_text = "\n\n".join(
            f"[{chunk.engine}] {chunk.source}\n{chunk.content}" for chunk in best
        )
        return {
            "query": query,
            "strategy": quotas,
            "chunks": [
                {
                    "engine": c.engine,
                    "source": c.source,
                    "score": round(c.score, 3),
                    "content": c.content,
                    "metadata": c.metadata,
                }
                for c in best
            ],
            "context_text": context_text,
        }

    def run_with_sgpt(self, query: str, options: list[str] | None = None) -> dict[str, object]:
        """Uses existing ShellGPT interface for request/response."""
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
