from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.model_intelligence.common import ModelAnalysisError
from worker.model_intelligence.quantization_analyzer import (
    QuantizationAnalyzer,
)
from worker.model_intelligence.static_tensor_analyzer import (
    StaticTensorAnalyzer,
)
from worker.model_intelligence.tokenizer_analyzer import TokenizerAnalyzer


def _safetensors(
    path: Path,
    tensors: dict[str, tuple[str, list[int], bytes]],
) -> None:
    header: dict[str, object] = {}
    payload = bytearray()
    for name, (dtype, shape, content) in tensors.items():
        start = len(payload)
        payload.extend(content)
        header[name] = {
            "dtype": dtype,
            "shape": shape,
            "data_offsets": [start, len(payload)],
        }
    encoded = json.dumps(
        header,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(len(encoded).to_bytes(8, "little") + encoded + payload)


def test_static_analysis_is_deterministic_and_reads_header_topology(
    tmp_path: Path,
) -> None:
    _safetensors(
        tmp_path / "model.safetensors",
        {
            "model.layers.0.weight": ("F32", [2], b"\0" * 8),
            "model.layers.1.bias": ("F16", [2], b"\0" * 4),
        },
    )
    analyzer = StaticTensorAnalyzer()

    first = analyzer.analyze(
        snapshot_root=tmp_path,
        weight_files=("model.safetensors",),
    ).to_dict()
    repeated = analyzer.analyze(
        snapshot_root=tmp_path,
        weight_files=("model.safetensors",),
    ).to_dict()

    assert first == repeated
    assert first["tensor_count"] == 2
    assert first["parameter_count"] == 4
    assert first["dtypes"] == {"F16": 1, "F32": 1}


def test_static_analysis_rejects_payload_offsets_outside_file(
    tmp_path: Path,
) -> None:
    header = {
        "weight": {
            "dtype": "F32",
            "shape": [2],
            "data_offsets": [0, 8],
        }
    }
    encoded = json.dumps(header).encode("utf-8")
    (tmp_path / "broken.safetensors").write_bytes(
        len(encoded).to_bytes(8, "little") + encoded
    )

    with pytest.raises(ModelAnalysisError) as captured:
        StaticTensorAnalyzer().analyze(
            snapshot_root=tmp_path,
            weight_files=("broken.safetensors",),
        )

    assert captured.value.code == "safetensors_offsets_out_of_bounds"


def test_tokenizer_analysis_reports_metadata_without_template_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "tokenizer.json").write_text(
        json.dumps(
            {
                "model": {"vocab": {"safe": 0, "unsafe": 1}},
                "normalizer": {"type": "Lowercase"},
                "pre_tokenizer": {"type": "Whitespace"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "model_max_length": 128,
                "chat_template": "{{ messages }}",
                "bos_token": "<s>",
            }
        ),
        encoding="utf-8",
    )

    result = TokenizerAnalyzer().analyze(snapshot_root=tmp_path).to_dict()

    assert result["vocabulary_size"] == 2
    assert result["normalizer"] == "Lowercase"
    assert result["prompt_template_status"] == "available"
    assert result["prompt_template_digest"]
    assert "{{ messages }}" not in json.dumps(result)


def test_quantization_analysis_distinguishes_missing_and_inconsistent(
    tmp_path: Path,
) -> None:
    missing = QuantizationAnalyzer().analyze(
        snapshot_root=tmp_path
    ).to_dict()
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "quantization_config": {
                    "quant_method": "gptq",
                    "bits": 32,
                    "group_size": 128,
                }
            }
        ),
        encoding="utf-8",
    )
    inconsistent = QuantizationAnalyzer().analyze(
        snapshot_root=tmp_path
    ).to_dict()

    assert missing["status"] == "not_available"
    assert inconsistent["status"] == "failed"
    assert inconsistent["reason_code"] == "quantization_metadata_inconsistent"
