from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import settings
from agent.hybrid_repository_scan import tracked_code_files

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
