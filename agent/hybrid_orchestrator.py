from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import importlib
import importlib.util
import logging
import re
import subprocess
from typing import Any


LOGGER = logging.getLogger(__name__)


MAX_SYMBOL_FILE_BYTES = 256_000
MAX_SYMBOL_FILES = 500
MAX_AGENT_OUTPUT_CHARS = 4_000


def _optional_import(module_name: str) -> Any | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        return None

    if spec is None:
        return None

    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - defensive for partial installs
        LOGGER.warning("Konnte optionales Modul %s nicht laden: %s", module_name, exc)
        return None


@dataclass
class SymbolEntry:
    file_path: str
    symbol: str
    kind: str
    line: int


@dataclass
class ContextPayload:
    symbol_hits: list[SymbolEntry] = field(default_factory=list)
    semantic_chunks: list[str] = field(default_factory=list)
    agent_findings: list[str] = field(default_factory=list)
    engine_mix: list[str] = field(default_factory=list)


class RepositoryMapEngine:
    """Engine A: leichtgewichtige Aider-artige Repository-Map mit Tree-Sitter-Fallback."""

    def __init__(self, repo_path: str | Path, max_files: int = MAX_SYMBOL_FILES) -> None:
        self.repo_path = Path(repo_path)
        self.max_files = max_files
        self._git = _optional_import("git")
        self._tree_sitter = _optional_import("tree_sitter")
        self._tree_sitter_languages = _optional_import("tree_sitter_languages")
        self._symbol_cache: list[SymbolEntry] = []

    def build_symbol_graph(self) -> None:
        tracked_files = self._discover_code_files()

        symbol_entries: list[SymbolEntry] = []
        for file_path in tracked_files[: self.max_files]:
            symbol_entries.extend(self._extract_symbols(file_path))

        self._symbol_cache = symbol_entries
        LOGGER.info("RepositoryMapEngine: %s Symbole indiziert", len(self._symbol_cache))

    def _discover_code_files(self) -> list[Path]:
        if self._git is not None:
            try:
                repo = self._git.Repo(self.repo_path)
                files = [self.repo_path / f for f in repo.git.ls_files().splitlines()]
                return [file_path for file_path in files if self._is_source_file(str(file_path))]
            except Exception:
                LOGGER.info("GitPython nicht nutzbar, fallback auf Dateisystemscan")

        return [
            path
            for path in self.repo_path.rglob("*")
            if path.is_file() and ".git" not in path.parts and self._is_source_file(str(path))
        ]

    def query(self, query: str, top_k: int = 20) -> list[SymbolEntry]:
        if not self._symbol_cache:
            self.build_symbol_graph()

        tokens = {t.lower() for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", query)}
        scored: list[tuple[int, SymbolEntry]] = []

        for entry in self._symbol_cache:
            searchable = f"{entry.symbol} {entry.file_path}".lower()
            score = sum(1 for token in tokens if token in searchable)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    @staticmethod
    def _is_source_file(file_name: str) -> bool:
        return file_name.endswith((".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".cpp", ".c", ".h"))

    def _extract_symbols(self, file_path: Path) -> list[SymbolEntry]:
        if not file_path.exists() or file_path.stat().st_size > MAX_SYMBOL_FILE_BYTES:
            return []

        text = file_path.read_text(encoding="utf-8", errors="ignore")
        language = file_path.suffix.lstrip(".")
        symbols = self._extract_with_tree_sitter(text, language)
        if symbols:
            return [
                SymbolEntry(file_path=str(file_path.relative_to(self.repo_path)), symbol=sym["name"], kind=sym["kind"], line=sym["line"])
                for sym in symbols
            ]

        return self._extract_with_regex(text, file_path)

    def _extract_with_tree_sitter(self, text: str, language: str) -> list[dict[str, Any]]:
        if self._tree_sitter is None or self._tree_sitter_languages is None:
            return []

        try:
            parser = self._tree_sitter.Parser()
            ts_language = self._tree_sitter_languages.get_language(language)
            parser.set_language(ts_language)
            tree = parser.parse(text.encode("utf-8"))
        except Exception:
            return []

        symbol_kinds = {
            "function_definition": "function",
            "class_definition": "class",
            "method_definition": "method",
            "function_declaration": "function",
            "class_declaration": "class",
        }
        results: list[dict[str, Any]] = []

        cursor = tree.walk()
        stack = [cursor.node]
        while stack:
            node = stack.pop()
            kind = symbol_kinds.get(node.type)
            if kind:
                name = self._resolve_symbol_name(node, text)
                if name:
                    results.append({"name": name, "kind": kind, "line": node.start_point[0] + 1})
            stack.extend(node.children)

        return results

    @staticmethod
    def _resolve_symbol_name(node: Any, source: str) -> str | None:
        for child in node.children:
            if child.type in {"identifier", "name", "type_identifier", "property_identifier"}:
                return source[child.start_byte:child.end_byte]
        return None

    def _extract_with_regex(self, text: str, file_path: Path) -> list[SymbolEntry]:
        regexes = [
            (re.compile(r"^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE), "function"),
            (re.compile(r"^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE), "class"),
            (re.compile(r"^\s*function\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE), "function"),
        ]
        entries: list[SymbolEntry] = []
        for regex, kind in regexes:
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                entries.append(
                    SymbolEntry(
                        file_path=str(file_path.relative_to(self.repo_path)),
                        symbol=match.group(1),
                        kind=kind,
                        line=line,
                    )
                )
        return entries


class AgenticSearchEngine:
    """Engine B: Vibe-inspirierte Dateisystemsuche über sichere Shell-Kommandos."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path)

    def search(self, query: str, limit: int = 3) -> list[str]:
        commands = [
            ["bash", "-lc", f"rg -n --hidden --glob '!.git' --max-count 30 {self._quote(query)} {self.repo_path}"] ,
            ["bash", "-lc", f"rg --files {self.repo_path} | head -n 50"],
            ["bash", "-lc", f"find {self.repo_path} -maxdepth 2 -type d | head -n 50"],
        ]
        findings: list[str] = []
        for command in commands[:limit]:
            output = self._run_command(command)
            if output:
                findings.append(output[:MAX_AGENT_OUTPUT_CHARS])
        return findings

    @staticmethod
    def _quote(query: str) -> str:
        safe = query.replace("'", "'\\''")
        return f"'{safe}'"

    @staticmethod
    def _run_command(command: list[str]) -> str:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except Exception as exc:
            return f"Command failed: {exc}"
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip()
        return output


class SemanticSearchEngine:
    """Engine C: LlamaIndex-basierte semantische Suche für unstrukturierte Daten."""

    def __init__(self, knowledge_paths: list[str | Path] | None = None) -> None:
        self.knowledge_paths = [Path(p) for p in (knowledge_paths or [])]
        self._llama_index_core = _optional_import("llama_index.core")
        self._query_engine: Any | None = None

    def build_index(self) -> None:
        if self._llama_index_core is None or not self.knowledge_paths:
            return

        existing = [str(path) for path in self.knowledge_paths if path.exists()]
        if not existing:
            return

        reader = self._llama_index_core.SimpleDirectoryReader(input_files=existing)
        docs = reader.load_data()
        index = self._llama_index_core.VectorStoreIndex.from_documents(docs)
        self._query_engine = index.as_query_engine(similarity_top_k=5)

    def query(self, query: str) -> list[str]:
        if self._query_engine is None:
            self.build_index()

        if self._query_engine is None:
            return []

        response = self._query_engine.query(query)
        text = str(response)
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        return chunks[:5]


class ContextManager:
    """Entscheidet, wie Engines kombiniert werden sollen, ohne Voll-Context zu laden."""

    def __init__(self, symbol_engine: RepositoryMapEngine, semantic_engine: SemanticSearchEngine, agentic_engine: AgenticSearchEngine) -> None:
        self.symbol_engine = symbol_engine
        self.semantic_engine = semantic_engine
        self.agentic_engine = agentic_engine

    def get_context(self, query: str) -> ContextPayload:
        payload = ContextPayload()

        query_lower = query.lower()
        code_intent = any(token in query_lower for token in ["funktion", "class", "bug", "refactor", "python", "code", "api"])
        docs_intent = any(token in query_lower for token in ["doku", "pdf", "log", "beschreibung", "error", "stacktrace"])

        if code_intent:
            payload.symbol_hits = self.symbol_engine.query(query, top_k=15)
            payload.engine_mix.append("repository_map")

        if docs_intent or not payload.symbol_hits:
            payload.semantic_chunks = self.semantic_engine.query(query)
            if payload.semantic_chunks:
                payload.engine_mix.append("semantic_search")

        low_confidence = len(payload.symbol_hits) < 3 and len(payload.semantic_chunks) < 2
        if low_confidence:
            payload.agent_findings = self.agentic_engine.search(query, limit=2)
            if payload.agent_findings:
                payload.engine_mix.append("agentic_search")

        return payload


class HybridOrchestrator:
    """Initialisiert Engine A/B/C und liefert kombinierten, relevanten Kontext."""

    def __init__(self, repo_path: str | Path, knowledge_paths: list[str | Path] | None = None) -> None:
        self.repo_path = Path(repo_path)
        self.repository_engine = RepositoryMapEngine(repo_path=self.repo_path)
        self.semantic_engine = SemanticSearchEngine(knowledge_paths=knowledge_paths)
        self.agentic_engine = AgenticSearchEngine(repo_path=self.repo_path)
        self.context_manager = ContextManager(
            symbol_engine=self.repository_engine,
            semantic_engine=self.semantic_engine,
            agentic_engine=self.agentic_engine,
        )

    def get_relevant_context(self, query: str) -> dict[str, Any]:
        payload = self.context_manager.get_context(query)

        return {
            "query": query,
            "engine_mix": payload.engine_mix,
            "symbol_map": [entry.__dict__ for entry in payload.symbol_hits],
            "semantic_chunks": payload.semantic_chunks,
            "agentic_findings": payload.agent_findings,
            "summary": self._build_summary(payload),
        }

    @staticmethod
    def _build_summary(payload: ContextPayload) -> str:
        return (
            f"Engines: {', '.join(payload.engine_mix) or 'none'} | "
            f"Symbols: {len(payload.symbol_hits)} | "
            f"Semantic Chunks: {len(payload.semantic_chunks)} | "
            f"Agent Findings: {len(payload.agent_findings)}"
        )
