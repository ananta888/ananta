from __future__ import annotations

from devtools.check_markdown_links import _collect_refs, _normalize_path_token


def test_inline_code_path_collection_skips_shell_configuration_examples() -> None:
    text = "\n".join(
        [
            "`-PanantaDebugKeystorePath=/pfad/zur/debug.keystore`",
            "`ANANTA_DEBUG_KEYSTORE_PATH=/pfad/zur/debug.keystore`",
            "`/mnt/c/Users/pst/.android/debug.keystore`",
            "`~/.android/debug.keystore`",
        ]
    )

    assert _collect_refs(text) == set()


def test_inline_code_path_collection_keeps_project_relative_paths() -> None:
    assert _normalize_path_token("docs/testing.md") == "docs/testing.md"
    assert _normalize_path_token("./docs/testing.md") == "./docs/testing.md"
    assert _normalize_path_token("/docs/testing.md") == "/docs/testing.md"
