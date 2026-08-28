from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from worker.training.local_transformers_tokenizer import load_local_tokenizer


class _FakeAutoTokenizer:
    calls: list[tuple[str, dict[str, Any]]] = []

    @classmethod
    def from_pretrained(cls, path: str, **kwargs: Any) -> dict[str, Any]:
        cls.calls.append((path, kwargs))
        return {"loader": "auto"}


class _FakeFastTokenizer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.chat_template: str | None = None


def _transformers(monkeypatch: Any) -> None:
    module = ModuleType("transformers")
    module.AutoTokenizer = _FakeAutoTokenizer  # type: ignore[attr-defined]
    module.PreTrainedTokenizerFast = _FakeFastTokenizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", module)


def test_v5_tokenizer_snapshot_uses_bounded_local_compatibility_loader(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _transformers(monkeypatch)
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "tokenizer_class": "TokenizersBackend",
                "bos_token": "<bos>",
                "eos_token": "<eos>",
                "pad_token": "<pad>",
                "model_max_length": 2048,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "chat_template.jinja").write_text("{{ messages }}", encoding="utf-8")

    tokenizer = load_local_tokenizer(tmp_path)

    assert isinstance(tokenizer, _FakeFastTokenizer)
    assert tokenizer.kwargs["tokenizer_file"] == str(tmp_path / "tokenizer.json")
    assert tokenizer.kwargs["bos_token"] == "<bos>"
    assert tokenizer.chat_template == "{{ messages }}"
    assert _FakeAutoTokenizer.calls == []


def test_existing_tokenizers_keep_strict_auto_loader(tmp_path: Path, monkeypatch: Any) -> None:
    _transformers(monkeypatch)
    _FakeAutoTokenizer.calls.clear()
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "LlamaTokenizerFast"}),
        encoding="utf-8",
    )

    tokenizer = load_local_tokenizer(tmp_path)

    assert tokenizer == {"loader": "auto"}
    assert _FakeAutoTokenizer.calls == [
        (
            str(tmp_path),
            {"local_files_only": True, "trust_remote_code": False},
        )
    ]
