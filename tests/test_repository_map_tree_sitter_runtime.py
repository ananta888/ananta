from __future__ import annotations

from types import SimpleNamespace

from agent import repository_map_tree_sitter as runtime


class _FakeTree:
    root_node = object()


class _FakeParser:
    def __init__(self, language=None) -> None:
        self.language = language

    def parse(self, _source: bytes) -> _FakeTree:
        return _FakeTree()


class _FakeLanguage:
    def __init__(self, capsule) -> None:
        self.capsule = capsule


def test_specific_grammar_is_preferred_over_legacy_bundle(monkeypatch) -> None:
    imported: list[str] = []

    def fake_import(name: str):
        imported.append(name)
        if name == "tree_sitter":
            return SimpleNamespace(Language=_FakeLanguage, Parser=_FakeParser)
        if name == "tree_sitter_java":
            return SimpleNamespace(language=lambda: object())
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(runtime, "import_module", fake_import)

    result = runtime.resolve_tree_sitter_parser("java")

    assert result.parser is not None
    assert result.status.available is True
    assert result.status.strategy == "specific_grammar:tree_sitter_java"
    assert "tree_sitter_languages" not in imported


def test_incompatible_legacy_bundle_is_reported_as_unavailable(monkeypatch) -> None:
    def fake_import(name: str):
        if name == "tree_sitter":
            return SimpleNamespace(Language=_FakeLanguage, Parser=_FakeParser)
        if name == "tree_sitter_python":
            raise ModuleNotFoundError(name)
        if name == "tree_sitter_languages":
            return SimpleNamespace(
                get_parser=lambda _language: (_ for _ in ()).throw(
                    TypeError("incompatible language constructor")
                )
            )
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(runtime, "import_module", fake_import)

    result = runtime.resolve_tree_sitter_parser("python")

    assert result.parser is None
    assert result.status.available is False
    assert result.status.strategy == "none"
    assert "specific_grammar_unavailable:ModuleNotFoundError" in result.status.diagnostics
    assert "legacy_grammar_unavailable:TypeError" in result.status.diagnostics


def test_incomplete_test_double_does_not_create_false_positive(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "import_module",
        lambda _name: SimpleNamespace(),
    )

    result = runtime.resolve_tree_sitter_parser("python")

    assert result.parser is None
    assert result.status.available is False
    assert result.status.diagnostics == ("tree_sitter_core_unavailable:RuntimeError",)
