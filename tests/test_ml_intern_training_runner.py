import json

import pytest

from agent import ml_intern_training_runner as runner


def test_runner_loads_bounded_spec(tmp_path):
    dataset = tmp_path / "train.jsonl"
    dataset.write_text('{"instruction":"Hi","output":"Hello"}\n', encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({
            "job_id": "job-1",
            "backend": "peft_trl",
            "base_model": "local-model",
            "dataset_path": str(dataset),
            "output_dir": str(tmp_path / "out"),
            "lora_rank": 9999,
            "batch_size": 0,
        }),
        encoding="utf-8",
    )

    spec = runner._load_spec(spec_path)

    assert spec.backend == "peft_trl"
    assert spec.lora_rank == 256
    assert spec.batch_size == 1
    assert spec.output_dir.exists()


def test_runner_rejects_missing_dataset(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({
            "job_id": "job-1",
            "backend": "unsloth",
            "base_model": "local-model",
            "dataset_path": str(tmp_path / "missing.jsonl"),
            "output_dir": str(tmp_path / "out"),
        }),
        encoding="utf-8",
    )

    with pytest.raises(runner.RunnerError, match="dataset_path does not exist"):
        runner._load_spec(spec_path)


def test_runner_formats_instruction_and_chat_records():
    instruction = runner._format_record({
        "instruction": "Write JSON",
        "input": "todo",
        "output": "{\"ok\": true}",
    })
    chat = runner._format_record({
        "messages": [
            {"role": "system", "content": "Be strict"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
    })

    assert "### Instruction:" in instruction
    assert "### Response:" in instruction
    assert "user: Hi" in chat
    assert "assistant: Hello" in chat
