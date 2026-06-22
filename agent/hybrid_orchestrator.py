from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent.rag_query_normalizer import normalize_query_from_settings

from agent.config import settings
from agent.cli_backends.sgpt import run_llm_cli_command
from agent.hybrid_context_orchestration import collect_context_chunks, serialize_context_result
from agent.hybrid_context_support import (
    build_file_manifest,
    manifest_needs_reingest,
    read_manifest,
    redact_sensitive_text,
    write_manifest,
)
from agent.hybrid_repository_scan import tracked_code_files

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
        ".jsonl",
        ".json",
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
        ".hpp": "cpp",
        ".cs": "c_sharp",
        ".rb": "ruby",
        ".php": "php",
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
        max_files: int = 8000,
        max_symbols_per_file: int = 80,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_files = max_files
        self.max_symbols_per_file = max_symbols_per_file
        self._symbol_graph: dict[str, list[str]] = {}
        self._file_state: dict[str, tuple[float, int]] = {}
        self._last_scan_ts = 0.0

    @classmethod
    def language_support_matrix(cls) -> dict[str, dict[str, str]]:
        matrix: dict[str, dict[str, str]] = {}
        for ext in sorted(cls.CODE_EXTENSIONS):
            ts_lang = cls.TREE_SITTER_LANGUAGE_BY_EXT.get(ext, "")
            matrix[ext] = {
                "tree_sitter_language": ts_lang or "",
                "fallback": "regex",
            }
        return matrix

    def _tracked_files(self) -> list[Path]:
        return tracked_code_files(
            repo_root=self.repo_root,
            code_extensions=self.CODE_EXTENSIONS,
            max_files=self.max_files,
        )

    def _parser_for_file(self, file_path: Path):
        if Parser is None or get_parser is None:
            return None
        lang = self.TREE_SITTER_LANGUAGE_BY_EXT.get(file_path.suffix.lower())
        if not lang:
            return None
        try:
            return get_parser(lang)
        except Exception as e:
            logging.debug(f"Tree-sitter parser init failed for language '{lang}': {e}")
            return None

    @staticmethod
    def _decode_node_text(node, source: bytes) -> str:
        try:
            return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_symbols_tree_sitter(self, file_path: Path, text: str) -> list[str]:
        parser = self._parser_for_file(file_path)
        if parser is None:
            return []
        source = text.encode("utf-8", errors="ignore")
        try:
            tree = parser.parse(source)
        except Exception as e:
            logging.debug(f"Tree-sitter parse failed for file '{file_path}': {e}")
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

    @staticmethod
    def _extract_symbols_jsonl(text: str) -> list[str]:
        """Extract top-level keys and short string values from JSON/JSONL as symbols."""
        import json as _json
        symbols: list[str] = []
        seen: set[str] = set()
        for line in text.splitlines()[:30]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = _json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            for k, v in obj.items():
                if k not in seen:
                    seen.add(k)
                    symbols.append(str(k))
                if isinstance(v, str) and 2 < len(v) < 80 and v.strip() not in seen:
                    seen.add(v.strip())
                    symbols.append(v.strip())
            if len(symbols) >= 80:
                break
        return symbols[:80]

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

    @staticmethod
    def _normalize_path_label(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")

    def _path_focus_for_query(self, query: str, paths: list[str]) -> dict[str, object] | None:
        query_label = self._normalize_path_label(query)
        if not query_label:
            return None
        candidate_roots: dict[str, int] = {}
        for rel_path in paths:
            parts = [part for part in str(rel_path or "").replace("\\", "/").split("/") if part]
            for depth in (1, 2):
                if len(parts) < depth:
                    continue
                root = "/".join(parts[:depth])
                label = self._normalize_path_label(root)
                basename_label = self._normalize_path_label(parts[depth - 1])
                if not label or len(basename_label) < 4:
                    continue
                if label in query_label or basename_label in query_label:
                    candidate_roots[root] = max(candidate_roots.get(root, 0), len(label))
        # Apply configurable alias expansion: if a known alias keyword appears in the
        # query label, treat the mapped path prefixes as if they were named in the query.
        # Configured via settings.rag_path_focus_aliases (dict[str, list[str]]).
        alias_roots_set: set[str] = set()
        try:
            aliases = dict(getattr(settings, "rag_path_focus_aliases", None) or {})
        except Exception:
            aliases = {}
        for alias_keyword, alias_roots in aliases.items():
            if alias_keyword in query_label:
                for alias_root in list(alias_roots or []):
                    alias_label = self._normalize_path_label(str(alias_root))
                    if alias_root not in candidate_roots:
                        candidate_roots[alias_root] = len(alias_label)
                    alias_roots_set.add(str(alias_root))
        if not candidate_roots:
            return None
        roots = sorted(candidate_roots, key=lambda item: (-candidate_roots[item], item))
        preferred = [root for root in roots if "/" in root] or roots[:1]
        all_anchor_paths = self._anchor_paths_for_focus(roots, paths)
        alias_root_prefixes = tuple(f"{r.rstrip('/')}/" for r in alias_roots_set)
        alias_anchor_paths = [
            p for p in all_anchor_paths
            if any(p.startswith(pfx) for pfx in alias_root_prefixes) or p in alias_roots_set
        ]
        return {
            "id": "query-path-focus",
            "paths": tuple(f"{root.rstrip('/')}/" for root in roots),
            "preferred_paths": tuple(f"{root.rstrip('/')}/" for root in preferred),
            "anchor_paths": tuple(all_anchor_paths),
            "alias_anchor_paths": tuple(alias_anchor_paths),
            "min_results": min(4, max(2, len(preferred) + 1)),
        }

    def _anchor_paths_for_focus(self, roots: list[str], paths: list[str]) -> list[str]:
        path_set = set(paths)
        anchors: list[str] = []
        entrypoint_names = {
            "__init__.py",
            "cli.py",
            "main.py",
            "app.py",
            "index.ts",
            "index.js",
            "README.md",
            "readme.md",
        }
        for root in roots:
            root_prefix = f"{root.rstrip('/')}/"
            in_root = sorted(path for path in path_set if path.startswith(root_prefix))
            direct = [path for path in in_root if "/" not in path[len(root_prefix):].strip("/")]
            prioritized = [
                path for path in direct
                if Path(path).name in entrypoint_names or Path(path).stem == Path(root).name
            ]
            for path in [*prioritized, *direct, *in_root]:
                if path not in anchors:
                    anchors.append(path)
                if len(anchors) >= 4:
                    return anchors
        return anchors

    @staticmethod
    def _path_in_focus(path: str, focus: dict[str, object] | None, *, preferred_only: bool = False) -> bool:
        if not focus:
            return False
        prefixes = list(focus.get("preferred_paths") or []) if preferred_only else list(focus.get("paths") or [])
        normalized = str(path or "").replace("\\", "/")
        return any(normalized == str(prefix).rstrip("/") or normalized.startswith(str(prefix)) for prefix in prefixes)

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
            except Exception as e:
                logging.debug(f"Skipping file with unreadable stat '{file_path}': {e}")
                continue
            state = (stat.st_mtime, stat.st_size)
            if not force and self._file_state.get(rel) == state:
                continue
            self._file_state[rel] = state
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logging.debug(f"Failed reading source file '{file_path}': {e}")
                self._symbol_graph.pop(rel, None)
                continue
            if file_path.suffix.lower() in {".jsonl", ".json"}:
                symbols = self._extract_symbols_jsonl(text)
            else:
                symbols = self._extract_symbols_tree_sitter(file_path, text) or self._extract_symbols_regex(text)
            if symbols:
                self._symbol_graph[rel] = symbols
            else:
                self._symbol_graph.pop(rel, None)

        removed = set(self._file_state.keys()) - active
        for rel in removed:
            self._file_state.pop(rel, None)
            self._symbol_graph.pop(rel, None)

    _REPO_STOP_TOKENS: frozenset = frozenset({
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
        "mir", "dir", "ihm", "ihr", "uns", "ich", "du", "er", "sie", "wir",
        "und", "oder", "aber", "nicht", "auch", "noch", "von", "mit", "bei",
        "aus", "zur", "zum", "ist", "sind", "war", "wird", "hat", "haben",
        "auf", "in", "an", "zu", "am", "im", "als", "bitte", "mal",
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "has", "its", "was", "use", "one", "how", "our", "out", "that",
        "this", "with", "from", "have", "will", "been", "they", "their",
    })

    # Path-parts that mark a file as test material rather than source code.
    # Files under these directories are scored as evidence, not as the primary
    # explanation target — see search() Source-First Selector below.
    _REPO_TEST_PATH_MARKERS: tuple[str, ...] = (
        "/tests/", "/test/", "/benchmarks/", "/e2e/",
    )

    # Frontend test file patterns (Angular/Karma). The dot-prefixed
    # `.spec.` is the convention across frontend-angular/ — those files
    # test Angular components, they are not components themselves.
    _REPO_TEST_FILE_PATTERNS: tuple[str, ...] = (
        ".spec.", "_spec.py", "test_", "_test.go", "_test.js", "_test.ts",
    )

    # Top-level directories that contain Ananta's own implementation.
    # Files under these paths are the primary explanation target when
    # a query token matches their content. Files in OTHER top-level
    # paths (e.g. client_surfaces/blender/, client_surfaces/freecad/,
    # client_surfaces/eclipse_runtime/, voice_runtime/, scripts/) are
    # considered third-party integrations or runtime assets; they
    # match tokens by accident (e.g. blender/addon/tasks.py has
    # nothing to do with Ananta's task system) and must be demoted
    # when an Ananta-core file matches the same token.
    #
    # This is generic: any file under any Ananta-core directory is
    # preferred over a file with the same token in a third-party
    # integration directory. The set is the union of directories that
    # actually contain Ananta implementation files — see
    # ananta-hybrid-orchestrator source paths.
    _ANANTA_CORE_DIRS: frozenset = frozenset({
        "agent", "worker", "services", "frontend-angular",
        "heuristics", "architektur", "domains", "policies",
        "schemas", "config", "src", "tools", "rag-helper",
        "prompts", "migrations", "examples", "experiments",
        "devtools", "deploy", "docker", "tests",
    })
    # Top-level directories that contain Ananta's own client surfaces
    # (i.e. things Ananta ships to interact with the user). Files
    # here are also Ananta source, but co-exist with third-party
    # integrations in client_surfaces/. The selector must NOT demote
    # these — only the third-party client_surfaces subdirs.
    _ANANTA_CLIENT_SURFACE_DIRS: frozenset = frozenset({
        "operator_tui", "tui_runtime", "common",
    })

    def search(
        self,
        query: str,
        top_k: int = 5,
        allowed_paths: list[str] | None = None,
    ) -> list[ContextChunk]:
        self.build()
        if not self._symbol_graph:
            return []
        # CCRDS-009: with an active domain scope only candidates inside the
        # allowed paths are scored at all; without scope nothing changes.
        symbol_items = list(self._symbol_graph.items())
        if allowed_paths is not None:
            from agent.codecompass.domain_scope import is_path_within
            symbol_items = [
                (rel, syms) for rel, syms in symbol_items
                if is_path_within(rel, allowed_paths)
            ]
            if not symbol_items:
                return []
        tokens = {
            t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query)
            if len(t) >= 3 and t.lower() not in self._REPO_STOP_TOKENS
        }
        path_focus = self._path_focus_for_query(
            query,
            [str(path) for path, _symbols in symbol_items],
        )
        # Source-First Selector: when a query contains a domain-like token
        # (a non-stopword token of length ≥ 4), source files whose filename
        # stem contains that token outrank test files that merely mention
        # the token in their test_<token>_* symbol names. The bug being
        # fixed: a single token "codecompass" produced a top-1 result of
        # `tests/test_codecompass_trigger_mode.py` (8.4) above
        # `worker/retrieval/codecompass_budgeting.py` (3.4) because test
        # files accumulate more `test_codecompass_*` symbols than source
        # files have `*codecompass*` symbols — so test files beat source
        # files. See test_repository_map_source_first_selector.py.
        domain_stems: set[str] = {
            t for t in tokens if len(t) >= 4
        }
        candidates: list[ContextChunk] = []
        for rel_path, symbols in symbol_items:
            score = 0.0
            path_lower = rel_path.lower()
            sym_lower = [s.lower() for s in symbols]
            path_token_hits = 0
            for token in tokens:
                if token in path_lower:
                    score += 1.4
                    path_token_hits += 1
                score += sum(1.0 for sym in sym_lower if token in sym)
            if score <= 0:
                continue
            # Boost files whose filename stem matches ≥2 distinct query tokens:
            # incentivises source files over tangentially-matching test/util files.
            if path_token_hits >= 2:
                from pathlib import Path as _Path
                stem_tokens = set(re.findall(r"[a-z0-9]+", _Path(rel_path).stem.lower()))
                stem_hits = len(tokens.intersection(stem_tokens))
                if stem_hits >= 2:
                    score *= 1.0 + 0.5 * stem_hits
            # Source-First Selector: a 3x boost when the filename stem
            # contains a domain token from the query. This compensates for
            # the symbol-frequency bias that test files exploit: a test
            # file with 6 `test_codecompass_*` symbols accumulates
            # +6 points, but a source file with 2 `*codecompass*` symbols
            # accumulates +2. The stem boost flips the ranking back to
            # source-first. The boost is gated on at least one domain
            # token so generic queries are unaffected.
            #
            # Test files (under tests/ or starting with test_) are
            # excluded from the stem boost entirely — they are evidence
            # of behaviour, not the implementation. They still receive
            # their natural score so they appear later in the ranking
            # rather than being filtered out (callers may want to see
            # which tests cover the area).
            from pathlib import Path as _Path2
            stem_text = _Path2(rel_path).stem.lower()
            # Test files are detected by three signals, any of which is
            # sufficient:
            #   1. Path under a test directory (tests/, test/, …)
            #   2. Python-style test prefix (test_*.py)
            #   3. Frontend test file pattern (*.spec.ts, *.spec.js, …)
            # The third signal catches Angular/Karma convention which
            # does not use a path-marker; without it, *.spec.ts files
            # in frontend-angular/ would outrank real Angular components
            # for queries like "zeig mir die api routes".
            is_test_path = (
                any(
                    marker in path_lower
                    for marker in self._REPO_TEST_PATH_MARKERS
                )
                or stem_text.startswith("test_")
                or any(pat in rel_path.lower() for pat in self._REPO_TEST_FILE_PATTERNS)
            )
            stem_hit_domains = {d for d in domain_stems if d in stem_text}
            if stem_hit_domains and not is_test_path:
                score *= 1.0 + 2.0 * len(stem_hit_domains)
            elif is_test_path and not stem_hit_domains:
                # No domain match in the stem → file is collateral. Demote
                # test files so they cannot outrank source files that
                # actually implement the domain. Test files that DO match
                # the domain in their stem (e.g. test_codecompass_*.py)
                # keep their natural score: they document behaviour and
                # are useful as supporting context after source files.
                score *= 0.15
            else:
                # Test file with domain in its stem (e.g. test_codecompass_*.py
                # or app.routes.spec.ts) — natural score, no extra boost.
                # Fall through.
                pass

            # Third-party integration demote: files under top-level
            # directories that are NOT in _ANANTA_CORE_DIRS and not
            # under an Ananta client surface are third-party
            # integrations (e.g. client_surfaces/blender/, client_surfaces/
            # freecad/, client_surfaces/eclipse_runtime/, voice_runtime/,
            # scripts/, plugins/<external>/, …). When such a file
            # matches a query token that ALSO matches an Ananta-core
            # file, the third-party file is ranked lower because
            # blender/addon/tasks.py has nothing to do with Ananta's
            # task system even though the filename matches.
            #
            # The detection is: take the first path segment; if it is
            # not in _ANANTA_CORE_DIRS and not the literal "client_surfaces"
            # whose second segment is in _ANANTA_CLIENT_SURFACE_DIRS,
            # this is a third-party file. The demote is multiplicative
            # (×0.2) so it does not erase a strong symbol-hit on
            # the third-party file — it just stops the third-party
            # file from beating an Ananta-core file that has fewer
            # symbol hits.
            top_segment = rel_path.split("/", 1)[0] if "/" in rel_path else rel_path
            is_third_party = False
            if top_segment not in self._ANANTA_CORE_DIRS:
                if top_segment == "client_surfaces":
                    # client_surfaces/<subdir>/… — only Ananta if subdir
                    # is in _ANANTA_CLIENT_SURFACE_DIRS.
                    sub = rel_path.split("/", 2)
                    is_third_party = len(sub) >= 2 and sub[1] not in self._ANANTA_CLIENT_SURFACE_DIRS
                elif top_segment in {"docs", "artifacts", "data", "ci-artifacts",
                                      "autoimport-state", "project-workspaces",
                                      "todos", "test-reports", "logs",
                                      "reference_sources", "data_test",
                                      "ananta.egg-info", "secrets",
                                      "git-hooks", "node_modules",
                                      "__pycache__", "venv"}:
                    # Documentation, runtime data, build outputs, deps
                    # — not source code at all. These never answer
                    # architectural questions.
                    is_third_party = True
                elif top_segment not in {"scripts", "public-rendezvous",
                                          "website", "web", "examples",
                                          "experiments", "prompts"}:
                    # Everything else at the top level we don't know:
                    # treat as third-party to be safe. (We deliberately
                    # ALLOW the explicit allow-list above — scripts/ is
                    # tooling, public-rendezvous/ is an Ananta runtime
                    # asset, examples/ and experiments/ are first-party
                    # reference material.)
                    is_third_party = True
                # else: top_segment in {scripts, public-rendezvous, …} → Ananta
            if is_third_party:
                score *= 0.2
            if self._path_in_focus(rel_path, path_focus):
                score *= 2.4
                if self._path_in_focus(rel_path, path_focus, preferred_only=True):
                    score *= 1.35
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
        if path_focus:
            candidates_by_source = {chunk.source: chunk for chunk in candidates}
            anchor_paths = [
                str(path)
                for path in list(path_focus.get("anchor_paths") or [])
                if str(path).strip()
            ]
            symbol_by_path = dict(symbol_items)
            max_score = max([chunk.score for chunk in candidates], default=1.0)
            anchor_score = max_score * 0.72
            alias_anchor_set = set(path_focus.get("alias_anchor_paths") or [])
            try:
                alias_boost = float(getattr(settings, "rag_path_focus_alias_anchor_boost", None) or 0.85)
            except Exception:
                alias_boost = 0.85
            alias_anchor_score = max_score * alias_boost
            for anchor_path in anchor_paths:
                effective_score = alias_anchor_score if anchor_path in alias_anchor_set else anchor_score
                existing_anchor = candidates_by_source.get(anchor_path)
                if existing_anchor is not None:
                    existing_anchor.score = max(float(existing_anchor.score or 0.0), effective_score)
                    existing_anchor.metadata = {
                        **dict(existing_anchor.metadata or {}),
                        "path_focus_anchor": str(path_focus.get("id") or ""),
                    }
                    continue
                symbols = list(symbol_by_path.get(anchor_path) or [])
                symbol_summary = ", ".join(symbols[:20])
                file_content: str | None = None
                try:
                    anchor_file = self.repo_root / anchor_path
                    if anchor_file.exists() and anchor_file.is_file():
                        file_content = anchor_file.read_text(encoding="utf-8", errors="ignore")[:2000]
                except Exception:
                    pass
                if not file_content and not symbol_summary:
                    continue
                if file_content:
                    content_parts = [anchor_path]
                    if symbol_summary:
                        content_parts.append(f"Symbols: {symbol_summary}")
                    content_parts.append(file_content)
                    chunk_content = "\n".join(content_parts)
                else:
                    chunk_content = f"{anchor_path}\nSymbols: {symbol_summary}"
                is_alias = anchor_path in alias_anchor_set
                candidates.append(
                    ContextChunk(
                        engine="repository_map",
                        source=anchor_path,
                        content=chunk_content,
                        score=effective_score,
                        metadata={
                            "symbol_count": str(len(symbols)),
                            "path_focus_anchor": str(path_focus.get("id") or ""),
                            "alias_anchor": "true" if is_alias else "false",
                        },
                    )
                )
                candidates_by_source[anchor_path] = candidates[-1]

        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        if not path_focus:
            return ranked[:top_k]

        limit = max(1, int(top_k or 1))
        selected = ranked[:limit]
        selected_sources = {chunk.source for chunk in selected}
        focused = [
            chunk for chunk in ranked
            if chunk.source not in selected_sources and self._path_in_focus(chunk.source, path_focus)
        ]
        min_results = max(1, int(path_focus.get("min_results") or 1))
        current_focus_count = sum(1 for chunk in selected if self._path_in_focus(chunk.source, path_focus))
        for chunk in focused:
            if current_focus_count >= min_results:
                break
            if len(selected) >= limit:
                selected.pop()
            selected.append(chunk)
            selected_sources.add(chunk.source)
            current_focus_count += 1
        return sorted(selected, key=lambda c: c.score, reverse=True)[:limit]


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
                build_command=lambda _q: [
                    "rg",
                    "--files",
                    "-g",
                    "*.env",
                    "-g",
                    "*.json",
                    "-g",
                    "*.yaml",
                    "-g",
                    "*.yml",
                ],
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
            completed = subprocess.run(  # noqa: S603 - command is sanitized/allowlisted before execution
                args,
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                shell=False,
            )
        except Exception as e:
            logging.debug(f"Agentic command failed: {' '.join(args)} ({e})")
            return ""
        output = completed.stdout.strip() or completed.stderr.strip()
        return output[: self.max_output_chars]

    def _plan(self, query: str) -> list[SearchSkill]:
        matches = [skill for skill in self.skills if skill.trigger(query)]
        matches.sort(key=lambda skill: skill.priority)
        return matches[: self.max_commands]

    def _apply_scope(self, args: list[str], allowed_paths: list[str]) -> list[str] | None:
        """CCRDS-010: rewrite a planned command to search only allowed paths.

        ``rg`` gets the scoped paths as explicit positional targets (an
        existing trailing ``.`` is replaced); ``cat``/``ls`` targets must
        already lie inside the scope, otherwise the command is dropped.
        Query content can never widen the scope: the query is a single
        sanitized pattern argument, the paths are appended afterwards.
        """
        from agent.codecompass.domain_scope import is_path_within, normalize_repo_relative_path

        if not args:
            return None
        if args[0] == "rg":
            scoped = args[:-1] if args[-1] == "." else list(args)
            return scoped + sorted(allowed_paths)
        # cat/ls: every path argument must be inside the scope.
        for arg in args[1:]:
            if arg.startswith("-"):
                continue
            normalized = normalize_repo_relative_path(arg, repo_root=self.repo_root)
            if normalized is None or not is_path_within(normalized, allowed_paths):
                return None
        return list(args)

    def search(
        self,
        query: str,
        top_k: int = 3,
        allowed_paths: list[str] | None = None,
    ) -> list[ContextChunk]:
        if allowed_paths is not None and not allowed_paths:
            # Active scope without any allowed path: never search globally.
            return []
        planned = self._plan(query)
        max_steps = min(len(planned), self.max_commands, max(top_k, 1))
        chunks: list[ContextChunk] = []
        used_output = 0
        for skill in planned[:max_steps]:
            args = skill.build_command(query)
            if allowed_paths is not None:
                args = self._apply_scope(args, allowed_paths)
                if args is None:
                    continue
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


class ContextManager:
    """Versioned decision policy for engine routing and diversity-aware reranking."""

    def __init__(self, policy_version: str = "v1") -> None:
        self.policy_version = policy_version

    @staticmethod
    def _quota(name: str, fallback: int) -> int:
        try:
            return max(0, int(getattr(settings, name, fallback)))
        except (TypeError, ValueError):
            return fallback

    def route(self, query: str) -> dict[str, int]:
        q = query.lower()
        code_like = any(k in q for k in (
            "class", "function", "funktion", "bug", "stacktrace", "repo",
            "module", "modul", "python", ".py", "engine", "service", "tick",
            "agent", "autopilot", "methode", "klasse", "route", "controller",
            "implementier", "implement", "wie funktioniert", "wie arbeitet",
        ))
        docs_like = any(k in q for k in ("pdf", "doku", "documentation", "log", "readme", "spec"))
        fs_like = any(k in q for k in ("find", "suche", "where", "ls", "grep", "datei", "folder"))

        quotas = {"repository_map": 0, "codecompass_vector": 0, "semantic_search": 0, "agentic_search": 0}
        if code_like:
            quotas["repository_map"] += self._quota("rag_route_quota_code_repo", 12)
            quotas["codecompass_vector"] += self._quota("rag_route_quota_codecompass_vector", 6)
            quotas["semantic_search"] += self._quota("rag_route_quota_code_semantic", 2)
            quotas["agentic_search"] += 1
        if docs_like:
            quotas["semantic_search"] += self._quota("rag_route_quota_docs_semantic", 4)
            quotas["repository_map"] += self._quota("rag_route_quota_docs_repo", 2)
            quotas["codecompass_vector"] += self._quota("rag_route_quota_codecompass_vector_docs", 1)
        if fs_like:
            quotas["agentic_search"] += self._quota("rag_route_quota_fs_agentic", 3)
            quotas["repository_map"] += self._quota("rag_route_quota_fs_repo", 2)
        if all(v == 0 for v in quotas.values()):
            quotas = {
                "repository_map": self._quota("rag_route_quota_default_repo", 6),
                "codecompass_vector": self._quota("rag_route_quota_codecompass_vector_default", 4),
                "semantic_search": self._quota("rag_route_quota_default_semantic", 4),
                "agentic_search": 1,
            }
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
        engine_weights = {
            "repository_map": 1.2,
            "codecompass_vector": 0.65,
            "semantic_search": 0.85,
            "agentic_search": 0.75,
        }
        for chunk in chunks:
            text = f"{chunk.source}\n{chunk.content}".lower()
            lexical = sum(text.count(token) for token in tokens)
            weight = engine_weights.get(str(chunk.engine), 1.0)
            chunk.score = float(chunk.score) * weight + lexical * 0.25

        chunks = self._merge_same_source_chunks(chunks)
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

    @staticmethod
    def _merge_same_source_chunks(chunks: list[ContextChunk]) -> list[ContextChunk]:
        by_source: dict[str, ContextChunk] = {}
        for chunk in chunks:
            source = str(chunk.source or "")
            if not source:
                by_source[f"__empty__:{id(chunk)}"] = chunk
                continue
            existing = by_source.get(source)
            if existing is None:
                chunk.metadata = {
                    **dict(chunk.metadata or {}),
                    "cross_engine_signals": str(chunk.engine),
                }
                by_source[source] = chunk
                continue
            existing_signals = {
                item.strip()
                for item in str((existing.metadata or {}).get("cross_engine_signals") or existing.engine).split(",")
                if item.strip()
            }
            existing_signals.add(str(chunk.engine))
            existing.metadata = {
                **dict(existing.metadata or {}),
                "cross_engine_signals": ",".join(sorted(existing_signals)),
            }
            existing.score = max(float(existing.score), float(chunk.score)) + min(float(existing.score), float(chunk.score)) * 0.15
            if len(str(chunk.content or "")) > len(str(existing.content or "")) and float(chunk.score) >= float(existing.score) * 0.8:
                existing.content = chunk.content
        return list(by_source.values())


class HybridOrchestrator:
    """Central orchestrator for repository-map, semantic retrieval and agentic search."""

    SECRET_PATTERNS = [
        re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        re.compile(
            r"\b([A-Za-z0-9_]*(?:token|password|secret|apikey|api_key)[A-Za-z0-9_]*\s*[:=]\s*['\"]?[^'\"\s]{6,})", re.I
        ),
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
        codecompass_vector_enabled: bool | None = None,
        codecompass_vector_service: object | None = None,
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
        self.codecompass_vector_service = codecompass_vector_service
        vector_enabled = (
            bool(settings.codecompass_vector_enabled)
            if codecompass_vector_enabled is None
            else bool(codecompass_vector_enabled)
        )
        if self.codecompass_vector_service is None and vector_enabled:
            from agent.services.codecompass_vector_retrieval_service import (
                CodeCompassVectorRetrievalService,
            )
            from agent.services.codecompass_ranking_config_service import (
                CodeCompassRankingConfigService,
            )

            ranking_cfg = CodeCompassRankingConfigService(
                global_config=getattr(settings, "global_config", None) or {},
            ).resolve()
            strategy_cfg = ranking_cfg.to_strategy_config()

            # Wire the restricted inference service when a transformer strategy is configured.
            restricted_inference = None
            if strategy_cfg.wants_prefilter():
                try:
                    from agent.services.restricted_model_inference_service import (
                        RestrictedModelInferenceService,
                    )
                    restricted_inference = RestrictedModelInferenceService()
                except Exception:
                    pass  # degrade gracefully — prefilter skipped if unavailable

            vector_encoding_config = {
                "mode": getattr(settings, "codecompass_vector_encoding_mode", "off"),
                "target_bits": getattr(settings, "codecompass_vector_encoding_target_bits", 32.0),
                "seed": getattr(settings, "codecompass_vector_encoding_seed", 888),
                "block_size": getattr(settings, "codecompass_vector_encoding_block_size", 0),
                "store_original": getattr(settings, "codecompass_vector_encoding_store_original", False),
            }
            self.codecompass_vector_service = CodeCompassVectorRetrievalService(
                repo_root=self.repo_root,
                embedding_records_path=settings.codecompass_vector_embedding_records_path,
                manifest_path=settings.codecompass_vector_manifest_path,
                index_path=settings.codecompass_vector_index_path,
                provider_config={"provider": "local_hash", "model_version": "hash-v1", "dimensions": 12},
                embedding_text_profile=settings.codecompass_vector_embedding_text_profile,
                fail_mode=settings.codecompass_vector_fail_mode,
                restricted_inference_service=restricted_inference,
                strategy_config=strategy_cfg,
                vector_encoding_config=vector_encoding_config,
                vector_encoding_fallback_policy=getattr(
                    settings, "codecompass_vector_encoding_fallback_policy", "fallback_float32"
                ),
            )
        self.context_manager = ContextManager(policy_version="v1")

        # HCCA-009: optional context compression adapter
        self._compression_adapter = None
        compression_cfg = dict(getattr(settings, "global_config", None) or {}).get("context_compression", {})
        if compression_cfg.get("enabled"):
            try:
                from agent.services.context_compression import build_compression_adapter
                self._compression_adapter = build_compression_adapter(compression_cfg)
            except Exception:
                pass  # compression is always optional, degrade gracefully

    def _compress_context_text(
        self, content: str, content_type: str = "rag_results", task_intent: str = ""
    ) -> str:
        """HCCA-010/011: Apply optional context compression to assembled context text."""
        if self._compression_adapter is None or not self._compression_adapter.is_enabled():
            return content
        try:
            from agent.services.context_compression import CompressionRequest
            req = CompressionRequest(
                content=content, content_type=content_type, task_intent=task_intent
            )
            result = self._compression_adapter.compress(req)
            return result.content
        except Exception:
            return content  # always safe passthrough on any error

    def _redact(self, text: str) -> str:
        if not self.redact_sensitive:
            return text
        return redact_sensitive_text(text, self.SECRET_PATTERNS)

    def _resolve_domain_scope(self, domain_scope: object) -> object | None:
        """CCRDS-007: accept a DomainScope or pre-resolved scope, or None."""
        if domain_scope is None:
            return None
        from agent.codecompass.domain_scope import DomainScope, ResolvedDomainScope
        if isinstance(domain_scope, ResolvedDomainScope):
            return domain_scope
        if isinstance(domain_scope, DomainScope):
            from agent.codecompass.domain_scope_resolver import DomainScopeResolver
            resolver = DomainScopeResolver(
                repo_root=self.repo_root,
                artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
                descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
            )
            return resolver.resolve(domain_scope)
        raise TypeError(f"unsupported domain_scope type: {type(domain_scope)!r}")

    def get_relevant_context(self, query: str, domain_scope: object | None = None) -> dict[str, object]:
        resolved_scope = self._resolve_domain_scope(domain_scope)
        scope_active = resolved_scope is not None and resolved_scope.active

        if scope_active and not resolved_scope.ok:
            # CCRDS-DD-003: strict resolution failure fails closed — no
            # global fallback, no context, explicit error for the caller.
            from agent.codecompass.domain_scope_filter import build_no_match_guidance
            return {
                "query": query,
                "error": "domain_scope_violation",
                "strategy": {},
                "policy_version": self.context_manager.policy_version,
                "chunks": [],
                "context_text": "",
                "token_estimate": 0,
                "domain_scope": {
                    **resolved_scope.as_dict(),
                    "guidance": build_no_match_guidance(resolved_scope),
                },
            }

        allowed_paths = list(resolved_scope.allowed_read_paths) if scope_active else None
        query_variants = normalize_query_from_settings(query)
        quotas = self.context_manager.route(query)

        # Collect chunks for original query plus any normalized variants.
        # Results are merged and deduplicated; original query keeps routing priority.
        all_chunks: list[ContextChunk] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for variant in query_variants:
            variant_chunks = collect_context_chunks(
                query=variant,
                quotas=quotas,
                repository_engine=self.repository_engine,
                semantic_engine=self.semantic_engine,
                agentic_engine=self.agentic_engine,
                codecompass_vector_service=self.codecompass_vector_service,
                allowed_paths=allowed_paths,
            )
            for chunk in variant_chunks:
                key = (chunk.engine, chunk.source, chunk.content[:120])
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_chunks.append(chunk)

        filter_stats = None
        if scope_active:
            from agent.codecompass.domain_scope_filter import filter_chunks
            all_chunks, filter_stats = filter_chunks(
                all_chunks, resolved_scope, repo_root=self.repo_root
            )

        # Re-score alias anchor chunks using the global max score (across all engines).
        # Alias anchors are injected during repo_engine.search() using only the repo-local
        # max score, which is far below semantic search scores. After collecting all chunks
        # we know the true global max and can give alias anchors a competitive score so
        # they survive the context budget selection.
        global_max_score = max((float(c.score or 0.0) for c in all_chunks), default=1.0)
        try:
            alias_boost = float(getattr(settings, "rag_path_focus_alias_anchor_boost", None) or 0.85)
        except Exception:
            alias_boost = 0.85
        for chunk in all_chunks:
            if dict(getattr(chunk, "metadata", {}) or {}).get("alias_anchor") == "true":
                chunk.score = global_max_score * alias_boost

        best = self.context_manager.rerank(
            chunks=all_chunks,
            query=query,
            max_chunks=self.max_chunks,
            max_chars=self.max_context_chars,
            max_tokens=self.max_context_tokens,
        )

        result = serialize_context_result(
            query=query,
            quotas=quotas,
            policy_version=self.context_manager.policy_version,
            chunks=best,
            redact=self._redact,
            estimate_tokens=self.context_manager.estimate_tokens,
            retrieval_diagnostics=self._retrieval_diagnostics(),
        )
        if scope_active:
            from agent.codecompass.domain_scope_filter import (
                build_no_match_guidance,
                build_scope_banner,
            )
            result["domain_scope"] = {
                **resolved_scope.as_dict(),
                "active_domain_ids": list(resolved_scope.selected_domain_ids),
                "filter_stats": filter_stats.as_dict() if filter_stats else None,
            }
            if not best:
                # CCRDS-014: empty in-scope result — explain instead of
                # silently widening the search.
                result["domain_scope"]["guidance"] = build_no_match_guidance(
                    resolved_scope, filter_stats
                )
            banner = build_scope_banner(resolved_scope, filter_stats)
            result["context_text"] = (
                f"{banner}\n\n{result['context_text']}" if result["context_text"] else banner
            )
        # HCCA-011: apply optional compression to the final assembled context text
        result["context_text"] = self._compress_context_text(
            result["context_text"], content_type="rag_results", task_intent=""
        )
        return result

    def _retrieval_diagnostics(self) -> dict[str, object]:
        diagnostics: dict[str, object] = {}
        if self.codecompass_vector_service is not None and hasattr(self.codecompass_vector_service, "last_diagnostic"):
            diagnostics["codecompass_vector"] = self.codecompass_vector_service.last_diagnostic()
        elif bool(getattr(settings, "codecompass_vector_enabled", False)):
            diagnostics["codecompass_vector"] = {"status": "degraded", "reason": "not_configured"}
        else:
            diagnostics["codecompass_vector"] = {"status": "disabled", "reason": "disabled"}
        return diagnostics

    def run_with_sgpt(
        self,
        query: str,
        options: list[str] | None = None,
        domain_scope: object | None = None,
    ) -> dict[str, object]:
        context = self.get_relevant_context(query, domain_scope=domain_scope)
        if context.get("error"):
            # Strict scope failure: no prompt is built, no LLM is called.
            return {
                "returncode": 1,
                "output": "",
                "errors": str(context["error"]),
                "backend": None,
                "context": context,
            }
        prompt = (
            "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\n"
            f"Frage:\n{query}\n\n"
            f"Kontext:\n{context['context_text']}"
        )
        rc, output, errors, backend_used = run_llm_cli_command(
            prompt=prompt,
            options=options or ["--no-interaction"],
            backend="auto",
        )
        return {
            "returncode": rc,
            "output": output,
            "errors": errors,
            "backend": backend_used,
            "context": context,
        }
