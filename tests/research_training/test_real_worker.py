from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("safetensors")

from ananta_contracts.research_training_execution import ResearchStageAssignmentV1
from worker.training.research.assignment_runner import ResearchAssignmentRunner
from worker.training.research.checkpoint import ResearchCheckpointManager
from worker.training.research.modeling import load_checkpoint
from worker.training.research.preemption import PreemptionController, ResearchStagePreempted
from worker.training.research.real_backend import LocalResearchBackend
from worker.training.research.runtime_verifier import EnvironmentResearchRuntimeVerifier
from worker.training.research.text_data import supervised_tokens
from worker.training.research.workspace import ResearchWorkspaceReader
from worker.training.tasks.code_sandbox import ContainerCodeEvaluationSandbox
from worker.training.tasks.registry import ResearchTaskRegistry
from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer, ByteBpeTrainer
from worker.training.tokenizers.evaluation import TokenizerEvaluator

from .real_helpers import assignment, dataset_manifest, persist_artifact, pipeline_spec, stage


def test_byte_bpe_is_deterministic_roundtrippable_and_digest_bound() -> None:
    texts = ["hello hello world", "Grüße aus Ananta", "<assistant>ok</assistant>"]
    trainer = ByteBpeTrainer()
    first = trainer.train(texts, vocab_size=272, special_tokens=["<assistant>", "</assistant>"])
    second = trainer.train(texts, vocab_size=272, special_tokens=["<assistant>", "</assistant>"])

    assert first.serialize() == second.serialize()
    assert [first.decode(first.encode(text)) for text in texts] == texts
    restored = ByteBpeTokenizer.from_bytes(first.serialize())
    assert restored == first
    report = TokenizerEvaluator().evaluate(first, splits={"validation": texts})
    assert report["unknown_token_rate"] == 0.0
    with pytest.raises(ValueError, match="digest_mismatch"):
        ByteBpeTokenizer.from_bytes(first.serialize(), expected_digest="0" * 64)


def test_supervised_mask_excludes_system_user_tool_and_role_framing() -> None:
    tokenizer = ByteBpeTrainer().train(
        ["system user secret assistant answer tool output"],
        vocab_size=264,
        special_tokens=[
            "<system>",
            "</system>",
            "<user>",
            "</user>",
            "<assistant>",
            "</assistant>",
            "<tool>",
            "</tool>",
        ],
    )
    prepared = supervised_tokens(
        {
            "messages": [
                {"role": "system", "content": "secret"},
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
                {"role": "tool", "content": "output"},
            ]
        },
        tokenizer,
    )
    trained_text = tokenizer.decode(
        [identifier for identifier, enabled in zip(prepared.token_ids, prepared.loss_mask, strict=True) if enabled]
    )
    assert trained_text == "answer"


def test_tiny_cpu_pretrain_sft_evaluation_benchmark_and_safe_export(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    definitions = [
        stage("tokenizer", "tokenizer_train", [], "tokenizer_training"),
        stage("pretrain", "pretrain", ["tokenizer"], "full_weight_training"),
        stage("base-eval", "base_eval", ["tokenizer", "pretrain"], "model_evaluation"),
        stage("sft", "sft", ["tokenizer", "pretrain"], "full_weight_training"),
        stage("chat-eval", "chat_eval", ["tokenizer", "sft"], "model_evaluation"),
        stage("rl", "rl", ["tokenizer", "sft"], "rl_training"),
        stage("rl-eval", "rl_eval", ["tokenizer", "rl"], "model_evaluation"),
        stage("benchmark", "inference_benchmark", ["tokenizer", "sft"], "inference_benchmark"),
        stage("export", "export", ["sft"], "model_export"),
    ]
    spec = pipeline_spec(dataset, definitions)
    backend = LocalResearchBackend(ResearchWorkspaceReader(tmp_path, maximum_input_bytes=20_000_000))
    runner = ResearchAssignmentRunner(backend)

    token_result = runner.execute(
        assignment(spec=spec, dataset=dataset, stage_definition=definitions[0], inputs=[])
    )
    token_input = persist_artifact(tmp_path, "tokenizer", token_result["content"])
    base_result = runner.execute(
        assignment(spec=spec, dataset=dataset, stage_definition=definitions[1], inputs=[token_input])
    )
    base_input = persist_artifact(tmp_path, "base_checkpoint", base_result["content"])
    model, config = load_checkpoint(base_result["content"])
    assert config.hidden_size == 32
    assert next(model.parameters()).numel() > 0

    base_eval = runner.execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definitions[2],
            inputs=[token_input, base_input],
        )
    )
    assert base_eval["metrics"]["bits_per_byte"] > 0
    sft_result = runner.execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definitions[3],
            inputs=[token_input, base_input],
        )
    )
    assert sft_result["content"] != base_result["content"]
    sft_input = persist_artifact(tmp_path, "sft_checkpoint", sft_result["content"])
    chat_eval = runner.execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definitions[4],
            inputs=[token_input, sft_input],
        )
    )
    assert chat_eval["metrics"]["loss"] > 0
    rl_result = runner.execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definitions[5],
            inputs=[token_input, sft_input],
            parameters={
                "rl_config": {
                    "schema": "ananta.research-training-rl-config.v1",
                    "algorithm": "reinforce_v1",
                    "samples_per_prompt": 1,
                    "maximum_new_tokens": 1,
                    "temperature": 1.0,
                    "learning_rate": 0.0001,
                    "maximum_steps": 1,
                    "seed": 7,
                    "reward": {
                        "schema": "ananta.research-training-reward.v1",
                        "reward_id": "exact-match",
                        "reward_version": "v1",
                        "provider": "exact_match_v1",
                        "maximum_absolute_reward": 1.0,
                        "redact_rollouts": True,
                    },
                }
            },
        )
    )
    assert 0.0 <= rl_result["metrics"]["reward_mean"] <= 1.0
    rl_input = persist_artifact(tmp_path, "rl_checkpoint", rl_result["content"])
    rl_eval = runner.execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definitions[6],
            inputs=[token_input, rl_input],
        )
    )
    assert rl_eval["metrics"]["loss"] > 0
    benchmark = runner.execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definitions[7],
            inputs=[token_input, sft_input],
            parameters={"prompts": ["hello"], "repetitions": 2, "maximum_new_tokens": 2},
        )
    )
    assert benchmark["metrics"]["throughput_tokens_s"] > 0
    exported = runner.execute(
        assignment(spec=spec, dataset=dataset, stage_definition=definitions[8], inputs=[sft_input])
    )
    assert exported["content"] != sft_result["content"]
    load_checkpoint(exported["content"])
    assert exported["manifest"]["executable"] is False


def test_assignment_rejects_unknown_input_kind_and_corrupt_workspace_artifact(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    definition = stage("pretrain", "pretrain", [], "full_weight_training")
    spec = pipeline_spec(dataset, [definition])
    invalid = persist_artifact(tmp_path, "unknown", b"not-a-model")
    with pytest.raises(ValueError, match="artifact_kind_invalid"):
        ResearchStageAssignmentV1.from_mapping(
            assignment(spec=spec, dataset=dataset, stage_definition=definition, inputs=[invalid])
        )
    valid = persist_artifact(tmp_path, "tokenizer", b"valid-at-first")
    (tmp_path / valid["relative_ref"]).write_bytes(b"mutated")
    reader = ResearchWorkspaceReader(tmp_path, maximum_input_bytes=1024)
    parsed = ResearchStageAssignmentV1.from_mapping(
        assignment(spec=spec, dataset=dataset, stage_definition=definition, inputs=[valid])
    )
    with pytest.raises(ValueError, match="(?:size|digest)_mismatch"):
        reader.read_artifact(parsed.inputs[0])


def test_assignment_rejects_runtime_from_a_different_repository_revision(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    definition = stage("tokenizer", "tokenizer_train", [], "tokenizer_training")
    spec = pipeline_spec(dataset, [definition])
    mismatched = assignment(
        spec=spec,
        dataset=dataset,
        stage_definition=definition,
        inputs=[],
    )
    mismatched["runtime"]["repository_revision"] = "f" * 64
    with pytest.raises(ValueError, match="revision_binding_invalid"):
        ResearchStageAssignmentV1.from_mapping(mismatched)


def test_worker_verifies_scheduler_runtime_identity_before_execution(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    definition = stage("tokenizer", "tokenizer_train", [], "tokenizer_training")
    spec = pipeline_spec(dataset, [definition])
    parsed = ResearchStageAssignmentV1.from_mapping(
        assignment(spec=spec, dataset=dataset, stage_definition=definition, inputs=[])
    )
    verifier = EnvironmentResearchRuntimeVerifier(
        repository_revision="a" * 64,
        image_digest="c" * 64,
        hardware_profile_digest="d" * 64,
    )
    verifier.configure_and_verify(parsed.runtime)
    mismatched = EnvironmentResearchRuntimeVerifier(
        repository_revision="a" * 64,
        image_digest="e" * 64,
        hardware_profile_digest="d" * 64,
    )
    with pytest.raises(PermissionError, match="image_digest"):
        mismatched.configure_and_verify(parsed.runtime)
    with pytest.raises(RuntimeError, match="runtime_identity_missing"):
        EnvironmentResearchRuntimeVerifier.from_environment({})


def test_task_registry_versions_sources_and_does_not_persist_samples() -> None:
    result = ResearchTaskRegistry().evaluate(
        task={
            "task_id": "arithmetic",
            "task_version": "v1",
            "task_kind": "numeric_tolerance",
            "group": "reasoning",
            "mandatory": True,
            "dataset_digest": "a" * 64,
            "source_refs": ["SRC_arithmetic"],
        },
        examples=[{"prompt": "2+2", "expected": "4", "tolerance": 0.0}],
        predictions=["4"],
    )
    assert result["score"] == 1.0
    assert result["samples_persisted"] is False
    assert result["result_digest"]


def test_training_preemption_atomically_hands_off_a_monotone_checkpoint(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    tokenizer = ByteBpeTrainer().train(
        ["hello world " * 4],
        vocab_size=264,
        special_tokens=["<assistant>", "</assistant>"],
    )
    token_input = persist_artifact(tmp_path, "tokenizer", tokenizer.serialize())
    definition = stage("pretrain", "pretrain", [], "full_weight_training")
    spec = pipeline_spec(dataset, [definition])
    controller = PreemptionController()
    controller.request()
    backend = LocalResearchBackend(
        ResearchWorkspaceReader(tmp_path, maximum_input_bytes=20_000_000),
        checkpoint_manager=ResearchCheckpointManager(
            tmp_path / "checkpoints", max_checkpoint_bytes=10_000_000
        ),
        preemption=controller,
    )
    with pytest.raises(ResearchStagePreempted) as caught:
        ResearchAssignmentRunner(backend).execute(
            assignment(
                spec=spec,
                dataset=dataset,
                stage_definition=definition,
                inputs=[token_input],
            )
        )
    receipt = caught.value.checkpoint_receipt
    assert receipt["optimizer_step"] == 1
    checkpoint_path = tmp_path / "checkpoints" / receipt["checkpoint_ref"]
    assert checkpoint_path.is_file()
    resumed_input = {
        "artifact_kind": "base_checkpoint",
        "artifact_digest": receipt["checkpoint_digest"],
        "size_bytes": receipt["size_bytes"],
        "relative_ref": checkpoint_path.relative_to(tmp_path).as_posix(),
    }
    resumed = ResearchAssignmentRunner(
        LocalResearchBackend(ResearchWorkspaceReader(tmp_path, maximum_input_bytes=20_000_000))
    ).execute(
        assignment(
            spec=spec,
            dataset=dataset,
            stage_definition=definition,
            inputs=[token_input, resumed_input],
            parameters={"resume_optimizer_step": 1},
        )
    )
    assert resumed["metrics"]["resumed_from_optimizer_step"] == 1.0
    assert resumed["metrics"]["optimizer_steps"] == 2.0


def test_code_evaluation_always_uses_a_resource_bounded_offline_container(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def execute(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 5
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    sandbox = ContainerCodeEvaluationSandbox(
        image="registry.local/eval@sha256:" + "a" * 64,
        executor=execute,
    )
    result = sandbox.run(
        workspace=tmp_path,
        command=["python", "test_submission.py"],
        timeout_seconds=5,
        memory_bytes=64 * 1024 * 1024,
    )
    argv = calls[0]
    assert result["status"] == "passed"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and "ALL" in argv and "no-new-privileges:true" in argv
    assert result["human_intervention_required"] is False

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)

    timed = ContainerCodeEvaluationSandbox(
        image="registry.local/eval@sha256:" + "a" * 64,
        executor=timeout,
    ).run(
        workspace=tmp_path,
        command=["pytest", "-q"],
        timeout_seconds=5,
        memory_bytes=64 * 1024 * 1024,
    )
    assert timed["status"] == "blocked"
    assert timed["reason_code"] == "research_code_sandbox_timeout"
