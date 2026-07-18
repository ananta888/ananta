from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Any


@dataclass(frozen=True, slots=True)
class TreeSitterRuntimeStatus:
    """Observed runtime state for one configured tree-sitter language."""

    language: str
    available: bool
    strategy: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TreeSitterParserResolution:
    """A parser together with the evidence used to advertise it."""

    parser: Any | None
    status: TreeSitterRuntimeStatus


_SPECIFIC_GRAMMARS: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": ("tree_sitter_python", ("language",)),
    "javascript": ("tree_sitter_javascript", ("language",)),
    "typescript": ("tree_sitter_typescript", ("language_typescript",)),
    "tsx": ("tree_sitter_typescript", ("language_tsx",)),
    "java": ("tree_sitter_java", ("language",)),
    "go": ("tree_sitter_go", ("language",)),
    "rust": ("tree_sitter_rust", ("language",)),
    "cpp": ("tree_sitter_cpp", ("language",)),
    "c": ("tree_sitter_c", ("language",)),
    "c_sharp": ("tree_sitter_c_sharp", ("language",)),
    "ruby": ("tree_sitter_ruby", ("language",)),
    "php": ("tree_sitter_php", ("language_php", "language")),
}


def _exception_code(prefix: str, exc: BaseException) -> str:
    return f"{prefix}:{type(exc).__name__}"


def _tree_sitter_core() -> tuple[Any, Any]:
    module = import_module("tree_sitter")
    language_cls = getattr(module, "Language", None)
    parser_cls = getattr(module, "Parser", None)
    if not callable(language_cls) or not callable(parser_cls):
        raise RuntimeError("tree_sitter_core_api_missing")
    return language_cls, parser_cls


def _parser_from_language_object(language_object: Any, parser_cls: Any) -> Any:
    try:
        parser = parser_cls()
        parser.language = language_object
        return parser
    except Exception as assignment_error:
        try:
            return parser_cls(language_object)
        except Exception:
            raise assignment_error


def _specific_parser(language: str, language_cls: Any, parser_cls: Any) -> tuple[Any, str]:
    module_name, entrypoint_names = _SPECIFIC_GRAMMARS[language]
    grammar_module: ModuleType = import_module(module_name)
    for entrypoint_name in entrypoint_names:
        entrypoint = getattr(grammar_module, entrypoint_name, None)
        if callable(entrypoint):
            language_object = language_cls(entrypoint())
            return _parser_from_language_object(language_object, parser_cls), module_name
    raise RuntimeError("grammar_entrypoint_missing")


def _legacy_parser(language: str) -> Any:
    legacy_module = import_module("tree_sitter_languages")
    get_parser = getattr(legacy_module, "get_parser", None)
    if not callable(get_parser):
        raise RuntimeError("legacy_get_parser_missing")
    return get_parser(language)


def _smoke_probe(parser: Any) -> None:
    parse = getattr(parser, "parse", None)
    if not callable(parse):
        raise RuntimeError("parser_parse_missing")
    tree = parse(b"\n")
    if getattr(tree, "root_node", None) is None:
        raise RuntimeError("parser_root_node_missing")


def resolve_tree_sitter_parser(language: str) -> TreeSitterParserResolution:
    """Resolve and smoke-test a parser without trusting installed-package metadata.

    The unmaintained ``tree_sitter_languages`` wheel is incompatible with some
    newer ``tree-sitter`` releases.  Language-specific grammar wheels therefore
    take precedence.  The legacy bundle remains a compatibility fallback, but a
    parser is advertised only after an actual parse probe succeeds.
    """

    normalized = str(language or "").strip().lower()
    if not normalized:
        return TreeSitterParserResolution(
            parser=None,
            status=TreeSitterRuntimeStatus(
                language="",
                available=False,
                strategy="none",
                diagnostics=("tree_sitter_language_not_configured",),
            ),
        )

    diagnostics: list[str] = []
    try:
        language_cls, parser_cls = _tree_sitter_core()
    except Exception as exc:
        logging.debug("Tree-sitter core probe failed for %s: %s", normalized, exc)
        return TreeSitterParserResolution(
            parser=None,
            status=TreeSitterRuntimeStatus(
                language=normalized,
                available=False,
                strategy="none",
                diagnostics=(_exception_code("tree_sitter_core_unavailable", exc),),
            ),
        )

    if normalized in _SPECIFIC_GRAMMARS:
        try:
            parser, module_name = _specific_parser(normalized, language_cls, parser_cls)
            _smoke_probe(parser)
            return TreeSitterParserResolution(
                parser=parser,
                status=TreeSitterRuntimeStatus(
                    language=normalized,
                    available=True,
                    strategy=f"specific_grammar:{module_name}",
                ),
            )
        except Exception as exc:
            logging.debug("Specific tree-sitter grammar probe failed for %s: %s", normalized, exc)
            diagnostics.append(_exception_code("specific_grammar_unavailable", exc))

    try:
        parser = _legacy_parser(normalized)
        _smoke_probe(parser)
        return TreeSitterParserResolution(
            parser=parser,
            status=TreeSitterRuntimeStatus(
                language=normalized,
                available=True,
                strategy="legacy_bundle:tree_sitter_languages",
                diagnostics=tuple(diagnostics),
            ),
        )
    except Exception as exc:
        logging.debug("Legacy tree-sitter grammar probe failed for %s: %s", normalized, exc)
        diagnostics.append(_exception_code("legacy_grammar_unavailable", exc))

    return TreeSitterParserResolution(
        parser=None,
        status=TreeSitterRuntimeStatus(
            language=normalized,
            available=False,
            strategy="none",
            diagnostics=tuple(diagnostics),
        ),
    )
