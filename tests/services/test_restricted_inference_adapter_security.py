from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from agent.services.model_inference_adapters.onnxruntime_adapter import OnnxRuntimeAdapter
from agent.services.model_inference_adapters.pytorch_adapter import PyTorchAdapter

_ADAPTER_ROOT = Path(__file__).resolve().parents[2] / "agent" / "services" / "model_inference_adapters"


def test_production_adapters_contain_no_generate_or_pickle_load_calls() -> None:
    violations: list[str] = []
    for path in sorted(_ADAPTER_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "generate":
                violations.append(f"{path.name}:{node.lineno}:generate")
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "torch"
                and node.func.attr == "load"
            ):
                violations.append(f"{path.name}:{node.lineno}:torch.load")
    assert violations == []


def test_pytorch_adapter_blocks_pickle_artifact_before_deserialization(tmp_path: Path) -> None:
    (tmp_path / "model.pkl").write_bytes(b"malicious pickle fixture")

    adapter = PyTorchAdapter(model_id=tmp_path, local_files_only=True)

    assert adapter.status().status == "degraded"
    assert adapter.status().error == "pickle_artifact_forbidden"


def test_onnx_adapter_requires_local_regular_onnx_file(tmp_path: Path) -> None:
    invalid = tmp_path / "model.pkl"
    invalid.write_bytes(b"not onnx")

    adapter = OnnxRuntimeAdapter(model_path=invalid, tokenizer_path=tmp_path)

    assert adapter.status().status == "degraded"
    assert adapter.status().error == "invalid_local_onnx_model"


def test_onnx_external_data_requires_exact_manifest_and_cannot_escape(tmp_path: Path, monkeypatch) -> None:
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(b"fixture")
    external_path = tmp_path / "weights.data"
    external_path.write_bytes(b"weights")

    def install_fake_onnx(location: str) -> None:
        onnx_module = ModuleType("onnx")
        onnx_module.TensorProto = SimpleNamespace(EXTERNAL=1)
        onnx_module.load = lambda *_args, **_kwargs: SimpleNamespace(
            graph=SimpleNamespace(
                initializer=[SimpleNamespace(data_location=1, location=location)]
            )
        )
        helper_module = ModuleType("onnx.external_data_helper")
        helper_module.ExternalDataInfo = lambda tensor: SimpleNamespace(location=tensor.location)
        monkeypatch.setitem(sys.modules, "onnx", onnx_module)
        monkeypatch.setitem(sys.modules, "onnx.external_data_helper", helper_module)

    install_fake_onnx("weights.data")
    missing_allowlist = OnnxRuntimeAdapter(model_path=model_path, tokenizer_path=tmp_path)
    assert missing_allowlist.status().error == "external_data_manifest_mismatch"

    install_fake_onnx("../outside.data")
    escaped = OnnxRuntimeAdapter(
        model_path=model_path,
        tokenizer_path=tmp_path,
        allowed_external_data=["../outside.data"],
    )
    assert escaped.status().error == "unsafe_external_data_path"
