from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from agent.services.model_inference_adapters.huggingface_transformers_adapter import (
    HuggingFaceTransformersAdapter,
)
from agent.services.model_inference_adapters.onnxruntime_adapter import OnnxRuntimeAdapter
from agent.services.model_inference_adapters.pytorch_adapter import PyTorchAdapter
from agent.services.model_inference_adapters.sentence_transformers_adapter import (
    SentenceTransformerBiEncoderAdapter,
    SentenceTransformerCrossEncoderAdapter,
)
from agent.services.restricted_inference_contract import (
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    RestrictedInferenceStatus,
)
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    ROLE_CONFIG,
    ROLE_TOKENIZER,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    RestrictedModelManifest,
)
from agent.services.restricted_inference_snapshot_store import SecureSnapshotStore
from worker.runtime.restricted_inference_admission import FilesystemSnapshotAdmission
from worker.runtime.restricted_inference_executor import build_default_executor
from worker.runtime.restricted_inference_resources import ResourceBudget, ResourceLeaseManager
from worker.runtime.restricted_inference_runtime import RestrictedInferenceWorkerRuntime


def _tiny_classifier(root: Path) -> None:
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("torch")
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "safe", "unsafe", "input"]
    root.mkdir()
    (root / "vocab.txt").write_text("\n".join(vocab) + "\n", encoding="utf-8")
    tokenizer = transformers.BertTokenizerFast(vocab_file=str(root / "vocab.txt"))
    tokenizer.save_pretrained(root)
    config = transformers.BertConfig(
        vocab_size=len(vocab),
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=16,
        num_labels=2,
        id2label={0: "safe", 1: "unsafe"},
        label2id={"safe": 0, "unsafe": 1},
    )
    model = transformers.BertForSequenceClassification(config)
    model.save_pretrained(root, safe_serialization=True)


def _manifest(root: Path) -> RestrictedModelManifest:
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        if path.suffix == ".safetensors":
            role = ROLE_WEIGHTS
        elif "token" in path.name or path.name == "vocab.txt" or path.name == "special_tokens_map.json":
            role = ROLE_TOKENIZER
        else:
            role = ROLE_CONFIG
        files.append(ModelManifestFile(relative, hashlib.sha256(content).hexdigest(), len(content), role))
    return RestrictedModelManifest(
        manifest_id="tiny-offline-classifier-v1",
        model_id="fixture/tiny-bert-classifier",
        engine=ENGINE_HUGGINGFACE,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef0123456789abcdef01234567",
        source_type=SOURCE_LOCAL_SNAPSHOT,
        license_id="Apache-2.0",
        operations=(RestrictedInferenceOperation.CLASSIFY,),
        files=tuple(files),
        tokenizer="tokenizer_config.json",
        ram_bytes=32 * 1024 * 1024,
        max_batch_size=4,
        max_sequence_length=32,
        metadata={"task": "sequence-classification", "labels": ["safe", "unsafe"]},
    )


def test_real_tiny_huggingface_model_runs_offline_through_worker_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    source = tmp_path / "source"
    _tiny_classifier(source)
    manifest = _manifest(source)
    snapshots = tmp_path / "snapshots"
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    SecureSnapshotStore(snapshot_root=snapshots).import_local(source, manifest)
    (manifests / f"{manifest.manifest_id}.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    resources = ResourceLeaseManager(
        ResourceBudget(
            max_ram_bytes=512 * 1024 * 1024,
            max_loaded_models=1,
            max_in_flight=1,
            max_queue=1,
        )
    )
    executor, _registry = build_default_executor(resources=resources)
    runtime = RestrictedInferenceWorkerRuntime(
        snapshot_admission=FilesystemSnapshotAdmission(
            manifest_root=manifests,
            snapshot_root=snapshots,
        ),
        executor=executor,
    )
    request = RestrictedInferenceRequest(
        request_id="request-real-1",
        task_id="task-real-1",
        run_id="run-real-1",
        tenant_id="tenant-real",
        operation=RestrictedInferenceOperation.CLASSIFY,
        payload={"text": "unsafe input", "labels": ["safe", "unsafe"]},
        model_manifest_id=manifest.manifest_id,
        policy_hash="policy-real",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )

    response = RestrictedInferenceResponse.from_dict(runtime.handle(request.to_dict()))

    assert response.status is RestrictedInferenceStatus.SUCCEEDED
    assert response.no_generation is True
    assert response.result is not None
    assert response.result["label"] in {"safe", "unsafe"}
    assert response.result["manifest_digest"] == manifest.digest
    assert set(response.result["all_scores"]) == {"safe", "unsafe"}


def test_multitoken_choice_scores_match_manual_forward_log_probabilities(tmp_path: Path, monkeypatch) -> None:
    transformers = pytest.importorskip("transformers")
    tokenizers = pytest.importorskip("tokenizers")
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    root = tmp_path / "tiny-causal"
    root.mkdir()
    vocabulary = {
        "[PAD]": 0,
        "[UNK]": 1,
        "[BOS]": 2,
        "[EOS]": 3,
        "question": 4,
        "yes": 5,
        "very": 6,
        "likely": 7,
    }
    raw_tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocabulary, unk_token="[UNK]"))
    raw_tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer = transformers.PreTrainedTokenizerFast(
        tokenizer_object=raw_tokenizer,
        pad_token="[PAD]",
        unk_token="[UNK]",
        bos_token="[BOS]",
        eos_token="[EOS]",
    )
    tokenizer.save_pretrained(root)
    config = transformers.GPT2Config(
        vocab_size=len(vocabulary),
        n_layer=1,
        n_head=2,
        n_embd=8,
        bos_token_id=vocabulary["[BOS]"],
        eos_token_id=vocabulary["[EOS]"],
        pad_token_id=vocabulary["[PAD]"],
    )
    model = transformers.GPT2LMHeadModel(config)
    model.save_pretrained(root, safe_serialization=True)
    adapter = HuggingFaceTransformersAdapter(
        model_id=str(root),
        task="causal-choice-scoring",
        local_files_only=True,
        max_seq_length=32,
    )
    choices = ["yes", "very likely"]

    returned = {item.choice: item.score for item in adapter.score_choices("question ", choices)}

    manual: dict[str, float] = {}
    model.eval()
    for choice in choices:
        prompt_ids = tokenizer("question ", add_special_tokens=True, return_tensors="pt")["input_ids"][0]
        full_ids = tokenizer("question " + choice, add_special_tokens=True, return_tensors="pt")["input_ids"][0]
        prefix = 0
        for left, right in zip(prompt_ids.tolist(), full_ids.tolist()):
            if left != right:
                break
            prefix += 1
        with torch.inference_mode():
            logits = model(input_ids=full_ids.unsqueeze(0)).logits[0]
        log_probs = torch.log_softmax(logits.float(), dim=-1)
        values = [float(log_probs[position - 1, int(full_ids[position])]) for position in range(prefix, len(full_ids))]
        manual[choice] = sum(values) / len(values)

    assert returned == pytest.approx(manual, rel=1e-6, abs=1e-6)


def test_real_tiny_sentence_transformers_cross_encoder_runs_offline(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("sentence_transformers")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    root = tmp_path / "tiny-cross-encoder"
    _tiny_classifier(root)
    adapter = SentenceTransformerCrossEncoderAdapter(
        model_path=str(root),
        device="cpu",
        max_seq_length=32,
        local_files_only=True,
    )

    results = adapter.rerank(
        "unsafe input",
        [
            {"path": "a.py", "record_id": "a", "excerpt": "safe"},
            {"path": "b.py", "record_id": "b", "excerpt": "unsafe input"},
        ],
    )

    assert adapter.status().status == "ready"
    assert {item.record_id for item in results} == {"a", "b"}
    assert all(0.0 <= item.score <= 1.0 for item in results)


def test_real_pytorch_adapter_uses_eval_and_inference_mode(tmp_path: Path, monkeypatch) -> None:
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    root = tmp_path / "tiny-pytorch-classifier"
    _tiny_classifier(root)
    adapter = PyTorchAdapter(
        model_id=root,
        task="sequence-classification",
        labels=["safe", "unsafe"],
        local_files_only=True,
    )
    original_forward = adapter._model.forward  # type: ignore[attr-defined]

    def checked_forward(*args, **kwargs):
        assert torch.is_grad_enabled() is False
        assert adapter._model.training is False  # type: ignore[attr-defined]
        return original_forward(*args, **kwargs)

    adapter._model.forward = checked_forward  # type: ignore[attr-defined]

    result = adapter.classify("unsafe input", ["safe", "unsafe"])

    assert adapter.status().status == "ready"
    assert result.label in {"safe", "unsafe"}


def test_real_huggingface_feature_extraction_uses_forward_hidden_states(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    root = tmp_path / "tiny-feature-model"
    _tiny_classifier(root)
    adapter = HuggingFaceTransformersAdapter(
        model_id=str(root),
        task="feature-extraction",
        local_files_only=True,
        max_seq_length=32,
    )

    vector = adapter.extract_features("safe input")

    assert adapter.status().status == "ready"
    assert vector.dimensions == 8
    assert len(vector.vector) == 8


def test_real_sentence_transformer_bi_encoder_returns_unit_norm_embedding(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("sentence_transformers")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    root = tmp_path / "tiny-bi-encoder"
    _tiny_classifier(root)
    adapter = SentenceTransformerBiEncoderAdapter(
        model_path=str(root),
        device="cpu",
        max_seq_length=32,
        normalize_embeddings=True,
        local_files_only=True,
    )

    vector = adapter.embed(["safe input"])[0]

    assert adapter.status().status == "ready"
    assert sum(value * value for value in vector) == pytest.approx(1.0, rel=1e-4, abs=1e-4)


def test_real_tiny_onnx_feature_model_runs_offline_on_cpu(tmp_path: Path, monkeypatch) -> None:
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    transformers = pytest.importorskip("transformers")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    root = tmp_path / "tiny-onnx-feature"
    root.mkdir()
    vocabulary = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "safe", "input"]
    (root / "vocab.txt").write_text("\n".join(vocabulary) + "\n", encoding="utf-8")
    tokenizer = transformers.BertTokenizerFast(vocab_file=str(root / "vocab.txt"))
    tokenizer.save_pretrained(root)

    helper = onnx.helper
    tensor = onnx.TensorProto
    graph = helper.make_graph(
        [
            helper.make_node("Cast", ["input_ids"], ["float_ids"], to=tensor.FLOAT),
            helper.make_node("Unsqueeze", ["float_ids", "axes"], ["hidden"]),
        ],
        "tiny-feature-extraction",
        [
            helper.make_tensor_value_info("input_ids", tensor.INT64, ["batch", "sequence"]),
            helper.make_tensor_value_info("attention_mask", tensor.INT64, ["batch", "sequence"]),
        ],
        [helper.make_tensor_value_info("hidden", tensor.FLOAT, ["batch", "sequence", 1])],
        initializer=[helper.make_tensor("axes", tensor.INT64, [1], [2])],
    )
    model = helper.make_model(
        graph,
        producer_name="ananta-test-fixture",
        opset_imports=[helper.make_opsetid("", 17)],
    )
    model.ir_version = 10
    model_path = root / "model.onnx"
    onnx.save(model, model_path)

    adapter = OnnxRuntimeAdapter(
        model_path=model_path,
        tokenizer_path=root,
        device="cpu",
        task="feature-extraction",
        max_seq_length=16,
        pooling="mean",
    )

    vector = adapter.extract_features("safe input")

    assert adapter.status().status == "ready"
    assert adapter.status().device == "cpu"
    assert vector.dimensions == 1
    assert len(vector.vector) == 1
    assert vector.vector[0] > 0
