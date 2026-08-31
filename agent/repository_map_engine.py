from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agent.codecompass.file_type_telemetry import (
    FileTypeTelemetryPort,
    emit_file_type_telemetry,
    observe_file_type_parser_result,
)
from agent.codecompass.parser_limits import ParserGuardViolation, ParserLimits
from agent.config import settings
from agent.hybrid_repository_scan import tracked_code_files, tracked_registry_files
from agent.repository_map_path_focus import path_is_in_focus, resolve_path_focus
from agent.repository_map_tree_sitter import resolve_tree_sitter_parser

_LEGACY_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php", ".jsonl", ".json",
}


def _load_repository_file_type_registry(repo_root: Path):
    try:
        from ananta_contracts.file_type_support import load_file_type_support_registry

        return load_file_type_support_registry(repo_root)
    except Exception as exc:
        logging.warning("CodeCompass file-type registry unavailable; using compatibility scan: %s", exc)
        return None


def _registry_repository_extensions() -> set[str]:
    registry = _load_repository_file_type_registry(Path(__file__).resolve().parents[1])
    if registry is None:
        return set(_LEGACY_CODE_EXTENSIONS)
    return {
        extension
        for descriptor in registry.descriptors
        if descriptor.enabled and descriptor.support_for("repository_map").indexed.configured
        for extension in descriptor.selectors.extensions
    } or set(_LEGACY_CODE_EXTENSIONS)


@dataclass(slots=True)
class ContextChunk:
    engine: str
    source: str
    content: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


class RepositoryMapEngine:
    """Aider-style repository symbol map with incremental cache invalidation."""

    # Compatibility surface for callers/tests. The active scanner also handles
    # exact filenames, patterns and shebangs through the same registry.
    CODE_EXTENSIONS = _registry_repository_extensions()
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
    VERIFIED_REGEX_FALLBACK_EXTENSIONS = {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
    }
    def __init__(
        self,
        repo_root: str | Path,
        max_files: int = 8000,
        max_symbols_per_file: int = 80,
        *,
        limits: ParserLimits | None = None,
        telemetry: FileTypeTelemetryPort | None = None,
        source_ranker=None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_files = max_files
        self.max_symbols_per_file = max_symbols_per_file
        self._symbol_graph: dict[str, list[str]] = {}
        self._import_graph: dict[str, tuple[str, ...]] = {}
        self._file_state: dict[str, tuple[float, int]] = {}
        self._tree_sitter_parser_cache: dict[str, object | None] = {}
        self._file_type_registry = _load_repository_file_type_registry(self.repo_root)
        self._limits = limits or ParserLimits.from_environment()
        self._telemetry = telemetry or observe_file_type_parser_result
        self._parser_diagnostics: dict[str, dict[str, object]] = {}
        self._last_scan_ts = 0.0
        self._last_ranking_trace: dict[str, object] = {}
        if source_ranker is None:
            from ananta_codecompass.ranking import UniversalSourceRanker

            source_ranker = UniversalSourceRanker()
        self._source_ranker = source_ranker

    def parser_diagnostics(self) -> tuple[dict[str, object], ...]:
        """Return deterministic, bounded diagnostics from the latest file states."""

        return tuple(dict(self._parser_diagnostics[path]) for path in sorted(self._parser_diagnostics))

    @classmethod
    def language_support_matrix(cls) -> dict[str, dict[str, object]]:
        registry = _load_repository_file_type_registry(Path(__file__).resolve().parents[1])
        descriptor_by_extension = {
            extension: descriptor
            for descriptor in (registry.descriptors if registry is not None else ())
            for extension in descriptor.selectors.extensions
        }
        matrix: dict[str, dict[str, object]] = {}
        for ext in sorted(cls.CODE_EXTENSIONS):
            ts_lang = cls.TREE_SITTER_LANGUAGE_BY_EXT.get(ext, "")
            resolution = resolve_tree_sitter_parser(ts_lang) if ts_lang else None
            tree_sitter_available = bool(
                resolution is not None and resolution.status.available
            )
            legacy_fallback = "json_keys" if ext in {".json", ".jsonl"} else "regex"
            if ext in {".json", ".jsonl"}:
                fallback_strategy = "json_keys"
            elif ext in cls.VERIFIED_REGEX_FALLBACK_EXTENSIONS:
                fallback_strategy = "regex_declarations_limited"
            else:
                fallback_strategy = "none"
            matrix[ext] = {
                "format_id": (
                    descriptor_by_extension[ext].format_id if ext in descriptor_by_extension else None
                ),
                "support_level": (
                    "symbol_index"
                    if ext in descriptor_by_extension
                    and descriptor_by_extension[ext].support_for("repository_map").symbols.verified
                    else "text_index"
                ),
                "parser_strategy": (
                    descriptor_by_extension[ext].parser_strategy if ext in descriptor_by_extension else None
                ),
                "known_limits": (
                    list(descriptor_by_extension[ext].known_limits) if ext in descriptor_by_extension else []
                ),
                "enabled": (
                    descriptor_by_extension[ext].enabled if ext in descriptor_by_extension else True
                ),
                "tree_sitter_language": ts_lang or "",
                "tree_sitter_configured": bool(ts_lang),
                "tree_sitter_available": tree_sitter_available,
                "tree_sitter_strategy": (
                    resolution.status.strategy if resolution is not None else "none"
                ),
                "tree_sitter_diagnostics": (
                    list(resolution.status.diagnostics) if resolution is not None else []
                ),
                "effective_parser": (
                    resolution.status.strategy
                    if tree_sitter_available
                    else fallback_strategy
                ),
                "fallback": legacy_fallback,
                "fallback_strategy": fallback_strategy,
                "fallback_verified": fallback_strategy != "none",
            }
        return matrix

    def _tracked_files(self) -> list[Path]:
        if self._file_type_registry is not None:
            return tracked_registry_files(
                repo_root=self.repo_root,
                registry=self._file_type_registry,
                pipeline="repository_map",
                max_files=self.max_files,
            )
        return tracked_code_files(
            repo_root=self.repo_root,
            code_extensions=self.CODE_EXTENSIONS,
            max_files=self.max_files,
        )

    def _parser_for_file(self, file_path: Path):
        extension = file_path.suffix.lower()
        if extension in self._tree_sitter_parser_cache:
            return self._tree_sitter_parser_cache[extension]
        lang = self.TREE_SITTER_LANGUAGE_BY_EXT.get(extension)
        if not lang:
            return None
        resolution = resolve_tree_sitter_parser(lang)
        parser = resolution.parser if resolution.status.available else None
        self._tree_sitter_parser_cache[extension] = parser
        if parser is None:
            logging.debug(
                "Tree-sitter parser unavailable for language '%s': %s",
                lang,
                ",".join(resolution.status.diagnostics),
            )
        return parser

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
            parse_started = time.perf_counter()
            fallback_reason: str | None = None
            try:
                with file_path.open("rb") as handle:
                    raw = handle.read(self._limits.max_file_bytes + 1)
                text = raw.decode("utf-8")
                self._limits.preflight(path=rel, content=text)
                budget = self._limits.budget()
                if file_path.suffix.lower() in {".jsonl", ".json"}:
                    symbols = self._extract_symbols_jsonl(text)
                else:
                    symbols = self._extract_symbols_tree_sitter(file_path, text)
                    if not symbols:
                        symbols = self._extract_symbols_regex(text)
                        fallback_reason = "parser_fallback"
                budget.check_time()
                budget.check_record_count(len(symbols))
                self._import_graph[rel] = self._extract_import_references(rel, text)
            except ParserGuardViolation as exc:
                logging.debug("Skipping repository-map input '%s': %s", file_path, exc)
                self._parser_diagnostics[rel] = exc.as_diagnostic(path=rel)
                self._symbol_graph.pop(rel, None)
                self._import_graph.pop(rel, None)
                self._observe_parser_result(
                    path=rel,
                    started=parse_started,
                    byte_size=stat.st_size,
                    outcome=(
                        "failed" if exc.diagnostic_code == "parser_timeout" else "excluded"
                    ),
                    diagnostics=(exc.diagnostic_code,),
                )
                continue
            except Exception as e:
                logging.debug(f"Failed reading source file '{file_path}': {e}")
                self._parser_diagnostics[rel] = {
                    "severity": "warning",
                    "code": "file_read_failed",
                    "reason_code": type(e).__name__,
                    "message": "Repository-map source could not be read safely.",
                    "path": rel,
                    "line": None,
                }
                self._symbol_graph.pop(rel, None)
                self._import_graph.pop(rel, None)
                self._observe_parser_result(
                    path=rel,
                    started=parse_started,
                    byte_size=stat.st_size,
                    outcome="failed",
                    diagnostics=("file_read_failed",),
                )
                continue
            self._parser_diagnostics.pop(rel, None)
            if symbols:
                self._symbol_graph[rel] = symbols
            else:
                self._symbol_graph.pop(rel, None)
            self._observe_parser_result(
                path=rel,
                started=parse_started,
                byte_size=stat.st_size,
                outcome="indexed",
                symbol_count=len(symbols),
                fallback_reason=fallback_reason,
            )

        removed = set(self._file_state.keys()) - active
        for rel in removed:
            self._file_state.pop(rel, None)
            self._symbol_graph.pop(rel, None)
            self._import_graph.pop(rel, None)
            self._parser_diagnostics.pop(rel, None)

    @staticmethod
    def _extract_import_references(source_path: str, text: str) -> tuple[str, ...]:
        """Extract bounded, explicit source dependency evidence.

        This deliberately records only import/include declarations present in
        source text. Directory proximity is not treated as graph evidence.
        """
        refs: set[str] = set()
        suffix = Path(source_path).suffix.lower()
        if suffix == ".py":
            for match in re.finditer(r"(?m)^\s*(?:from\s+([.\w]+)\s+import|import\s+([\w.]+))", text):
                refs.add(str(match.group(1) or match.group(2) or ""))
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            for match in re.finditer(r"(?:from\s+|import\s*\()[\"']([^\"']+)[\"']", text):
                refs.add(str(match.group(1) or ""))
        elif suffix in {".c", ".cpp", ".h", ".hpp"}:
            refs.update(match.group(1) for match in re.finditer(r"(?m)^\s*#include\s*[<\"]([^>\"]+)[>\"]", text))
        return tuple(sorted(ref for ref in refs if ref))[:100]

    @staticmethod
    def _resolve_import_target(source: str, imported: str, paths: set[str]) -> str | None:
        normalized = imported.replace(".", "/").strip("/")
        candidates: list[str] = []
        if imported.startswith("."):
            level = len(imported) - len(imported.lstrip("."))
            base = list(PurePosixPath(source).parent.parts)
            if level > 1:
                base = base[: -(level - 1)]
            normalized = imported.lstrip(".").replace(".", "/")
            candidates.append("/".join([*base, normalized]).strip("/"))
        else:
            candidates.append(normalized)
        expanded: list[str] = []
        for candidate in candidates:
            expanded.extend(
                (candidate, candidate + ".py", candidate + ".ts", candidate + ".tsx", candidate + "/__init__.py")
            )
        return next((candidate for candidate in expanded if candidate in paths), None)

    def _observe_parser_result(
        self,
        *,
        path: str,
        started: float,
        byte_size: int,
        outcome: str,
        symbol_count: int = 0,
        fallback_reason: str | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> None:
        emit_file_type_telemetry(
            self._telemetry,
            pipeline="repository_map",
            path=path,
            outcome=outcome,
            duration_seconds=time.perf_counter() - started,
            byte_size=byte_size,
            symbol_count=symbol_count,
            edge_count=0,
            fallback_reason=fallback_reason,
            diagnostics=diagnostics,
        )

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
        """Rank repository sources through the versioned universal strategy.

        Eligibility remains a hard pre-ranking scope boundary. The legacy
        strategy is retained only as an explicit deployment rollback or
        read-only shadow comparison.
        """
        from agent.services.codecompass_universal_ranking_profile_service import (
            get_codecompass_universal_ranking_profile_service,
        )

        policy = get_codecompass_universal_ranking_profile_service().resolve()
        strategy = policy.strategy
        if strategy == "legacy":
            return self._search_legacy(query, top_k=top_k, allowed_paths=allowed_paths)
        started = time.perf_counter()
        universal = self._search_universal(
            query,
            top_k=top_k,
            allowed_paths=allowed_paths,
            profile=policy.profile,
        )
        universal_latency_ms = (time.perf_counter() - started) * 1000.0
        self._last_ranking_trace["strategy"] = strategy
        self._last_ranking_trace["override_status"] = policy.override_status
        self._last_ranking_trace["latency_ms"] = round(universal_latency_ms, 3)
        if strategy == "shadow":
            from ananta_codecompass.ranking.shadow import compare_rankings

            baseline_started = time.perf_counter()
            legacy = self._search_legacy(query, top_k=top_k, allowed_paths=allowed_paths)
            baseline_latency_ms = (time.perf_counter() - baseline_started) * 1000.0
            self._last_ranking_trace["shadow"] = compare_rankings(
                universal_paths=[item.source for item in universal],
                baseline_paths=[item.source for item in legacy],
                repository_revision=str(self._last_ranking_trace.get("repository_revision") or "unknown"),
                index_digest=str(self._last_ranking_trace.get("index_digest") or "unknown"),
                ranking_version=str(self._last_ranking_trace.get("ranking_version") or "unknown"),
                latency_ms_universal=universal_latency_ms,
                latency_ms_baseline=baseline_latency_ms,
            ).as_dict()
        return universal

    def ranking_trace(self) -> dict[str, object]:
        return json.loads(json.dumps(self._last_ranking_trace, sort_keys=True))

    def _search_universal(
        self,
        query: str,
        *,
        top_k: int,
        allowed_paths: list[str] | None,
        profile,
    ) -> list[ContextChunk]:
        from ananta_codecompass.ranking import (
            RankingCandidate,
            RankingInput,
        )
        from ananta_codecompass.ranking.file_roles import language_for_path

        self.build()
        symbol_items = sorted(self._symbol_graph.items())
        if allowed_paths is not None:
            from agent.codecompass.domain_scope import is_path_within
            symbol_items = [
                (path, symbols) for path, symbols in symbol_items
                if is_path_within(path, allowed_paths)
            ]
        from ananta_codecompass.ranking.graph_features import derive_graph_features

        candidate_paths = {path for path, _symbols in symbol_items}
        edges: list[dict[str, str]] = []
        for source, imports in sorted(self._import_graph.items()):
            if source not in candidate_paths:
                continue
            for imported in imports:
                target = self._resolve_import_target(source, imported, candidate_paths)
                if target and target != source:
                    edges.append({"source": source, "target": target, "source_ref": f"{source}:import:{imported}"})
        query_tokens = {
            token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query)
            if len(token) >= 3 and token.lower() not in self._REPO_STOP_TOKENS
        }
        query_nodes = {
            path for path, symbols in symbol_items
            if any(
                token in path.lower() or any(token in symbol.lower() for symbol in symbols)
                for token in query_tokens
            )
        }
        graph_features = derive_graph_features(
            nodes=[{"id": path} for path in sorted(candidate_paths)],
            edges=edges,
            query_node_ids=query_nodes,
        )
        candidates = tuple(
            RankingCandidate(
                canonical_id=path,
                path=path,
                symbols=tuple(sorted(str(symbol) for symbol in symbols)),
                language=language_for_path(path),
                centrality=graph_features[path].centrality if graph_features[path].evidence_refs else None,
                graph_distance=graph_features[path].query_distance if graph_features[path].evidence_refs else None,
                relation_evidence=graph_features[path].evidence_refs,
            )
            for path, symbols in symbol_items
        )
        digest_payload = json.dumps(
            [(candidate.path, candidate.symbols) for candidate in candidates],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        index_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        result = self._source_ranker.rank(
            RankingInput(
                query=query,
                candidates=candidates,
                index_digest=index_digest,
                profile=profile,
                allowed_scope_digest=hashlib.sha256(
                    json.dumps(allowed_paths, sort_keys=True).encode("utf-8")
                ).hexdigest() if allowed_paths is not None else "unrestricted",
            ),
            top_k=top_k,
        )
        self._last_ranking_trace = result.as_dict()
        self._last_ranking_trace["graph_feature_coverage"] = {
            "edge_count": len(edges),
            "evidenced_candidates": sum(1 for item in graph_features.values() if item.evidence_refs),
            "candidate_count": len(candidates),
        }
        return [
            ContextChunk(
                engine="repository_map",
                source=item.candidate.path,
                content=(
                    f"{item.candidate.path}\nSymbols: "
                    + ", ".join(item.candidate.symbols[:20])
                ),
                score=item.score * 100.0,
                metadata={
                    "symbol_count": str(len(item.candidate.symbols)),
                    "ranking_version": result.ranking_version,
                    "ranking_profile": result.profile_id,
                    "ranking_profile_digest": result.profile_digest,
                    "file_role": item.file_role,
                    "ranking_explanation": json.dumps(
                        [
                            {
                                "signal": contribution.signal,
                                "normalized": contribution.normalized_value,
                                "weight": contribution.weight,
                                "contribution": contribution.contribution,
                            }
                            for contribution in item.contributions
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            )
            for item in result.ranked
        ]

    def _search_legacy(
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
        try:
            path_focus_aliases = dict(getattr(settings, "rag_path_focus_aliases", None) or {})
        except Exception:
            path_focus_aliases = {}
        path_focus = resolve_path_focus(
            query,
            [str(path) for path, _symbols in symbol_items],
            aliases=path_focus_aliases,
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
                # Repeated generated/helper symbols must not overwhelm a
                # direct path/stem match. Eight hits retain useful density
                # evidence without turning symbol count into relevance.
                score += min(8.0, sum(1.0 for sym in sym_lower if token in sym))
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
            if path_is_in_focus(rel_path, path_focus):
                score *= 2.4
                if path_is_in_focus(rel_path, path_focus, preferred_only=True):
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
            if chunk.source not in selected_sources and path_is_in_focus(chunk.source, path_focus)
        ]
        min_results = max(1, int(path_focus.get("min_results") or 1))
        current_focus_count = sum(1 for chunk in selected if path_is_in_focus(chunk.source, path_focus))
        for chunk in focused:
            if current_focus_count >= min_results:
                break
            if len(selected) >= limit:
                selected.pop()
            selected.append(chunk)
            selected_sources.add(chunk.source)
            current_focus_count += 1
        return sorted(selected, key=lambda c: c.score, reverse=True)[:limit]
