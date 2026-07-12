from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.services.model_inference_adapters import CAP_EMBEDDINGS, CAP_RERANK
from agent.services.model_inference_adapters.sentence_transformers_adapter import (
    SentenceTransformerBiEncoderAdapter,
    SentenceTransformerCrossEncoderAdapter,
)


class _BiModel:
    def __init__(self, model_path: str, **kwargs) -> None:
        assert Path(model_path).is_dir()
        assert kwargs["local_files_only"] is True
        assert kwargs["trust_remote_code"] is False
        self.max_seq_length = 0

    def encode(self, texts, **kwargs):
        assert kwargs["normalize_embeddings"] is True
        return [[0.6, 0.8] for _text in texts]


class _CrossModel:
    def __init__(self, model_path: str, **kwargs) -> None:
        assert Path(model_path).is_dir()
        assert kwargs["local_files_only"] is True
        assert kwargs["automodel_args"] == {"use_safetensors": True}

    def predict(self, pairs, **kwargs):
        return [0.5 for _pair in pairs]


def test_bi_encoder_and_cross_encoder_expose_separate_capabilities(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=_BiModel, CrossEncoder=_CrossModel),
    )
    bi_root = tmp_path / "bi"
    cross_root = tmp_path / "cross"
    bi_root.mkdir()
    cross_root.mkdir()

    bi = SentenceTransformerBiEncoderAdapter(model_path=str(bi_root), normalize_embeddings=True)
    cross = SentenceTransformerCrossEncoderAdapter(model_path=str(cross_root))

    assert bi.status().capabilities == frozenset({CAP_EMBEDDINGS, "feature_extraction"})
    assert cross.status().capabilities == frozenset({CAP_RERANK})
    assert bi.embed(["text"])[0] == pytest.approx([0.6, 0.8])


def test_cross_encoder_ties_are_resolved_by_path_then_record_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=_BiModel, CrossEncoder=_CrossModel),
    )
    root = tmp_path / "cross"
    root.mkdir()
    cross = SentenceTransformerCrossEncoderAdapter(model_path=str(root))

    results = cross.rerank(
        "query",
        [
            {"path": "z.py", "record_id": "z", "excerpt": "z"},
            {"path": "a.py", "record_id": "a", "excerpt": "a"},
        ],
    )

    assert [result.path for result in results] == ["a.py", "z.py"]
