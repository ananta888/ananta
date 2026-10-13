"""Complete bounded artifacts and generation coverage, using synthetic model doubles."""

import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from worker.training.backends.base import TrainingBackendError
from worker.training.evaluation import _iter_generated_comparisons
from worker.training.specialist_predictions import PREDICTION_ARTIFACT, persist_specialist_predictions


def sample(index):
    return {
        "id": hashlib.sha256(str(index).encode()).hexdigest(),
        "record_index": index,
        "winner": "adapter",
        "adapter_output": '{"label":"deny"}',
    }


def persist(root, samples, expected_records, check_cancelled=lambda: None):
    return persist_specialist_predictions(
        root, samples, expected_records=expected_records, check_cancelled=check_cancelled
    )


@pytest.mark.parametrize("failure", ["missing", "extra", "duplicate", "unordered", "invalid_id"])
def test_incomplete_or_ambiguous_coverage_removes_partial_artifact(tmp_path, failure):
    samples = [sample(0), sample(1)]
    if failure == "missing":
        samples.pop()
    elif failure == "extra":
        samples.append(sample(2))
    elif failure == "duplicate":
        samples[1]["id"] = samples[0]["id"]
    elif failure == "unordered":
        samples.reverse()
    else:
        samples[0]["id"] = "unbound"
    with pytest.raises(TrainingBackendError) as error:
        persist(tmp_path, samples, 2)
    assert error.value.code == "specialist_prediction_coverage_invalid"
    assert not (tmp_path / PREDICTION_ARTIFACT).exists()


def test_cancellation_during_stream_removes_partial_artifact(tmp_path):
    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        persist(tmp_path, [sample(0)], 1, cancelled)
    assert not (tmp_path / PREDICTION_ARTIFACT).exists()


def test_artifact_byte_quota_is_bounded_and_partial_file_removed(tmp_path, monkeypatch):
    monkeypatch.setattr("worker.training.specialist_predictions.MAX_PREDICTION_BYTES", 1)
    with pytest.raises(TrainingBackendError) as error:
        persist(tmp_path, [sample(0)], 1)
    assert error.value.code == "specialist_prediction_quota_exceeded"
    assert not (tmp_path / PREDICTION_ARTIFACT).exists()


def test_existing_artifact_is_never_replaced_or_removed(tmp_path):
    path = tmp_path / PREDICTION_ARTIFACT
    path.write_bytes(b"immutable previous output")
    with pytest.raises(TrainingBackendError) as error:
        persist(tmp_path, [sample(0)], 1)
    assert error.value.code == "specialist_prediction_artifact_exists"
    assert path.read_bytes() == b"immutable previous output"


@pytest.mark.parametrize("scorer,count", [("specialist_decision", 25), ("generic", 8)])
def test_real_generation_loop_covers_entire_specialist_input(scorer, count, tmp_path):
    context = SimpleNamespace(
        request=SimpleNamespace(
            configuration=SimpleNamespace(scorer_name=scorer, max_samples=25, max_sequence_length=256, seed=7)
        ),
        cancel=SimpleNamespace(raise_if_cancelled=lambda: None),
    )

    class Tensor:
        shape = (1, 3)

    class Tokenizer:
        pad_token_id = 0
        eos_token_id = 1

        def __call__(self, *args, **kwargs):
            return {"input_ids": Tensor()}

        def decode(self, *args, **kwargs):
            return '{"label":"deny"}'

    class Model:
        calls = 0

        def parameters(self):
            return iter(())

        def disable_adapter(self):
            return nullcontext()

        def generate(self, **kwargs):
            self.calls += 1
            return [[0, 0, 0, 1]]

    model = Model()
    rows = [{"instruction": f"case {index}", "output": '{"label":"deny"}'} for index in range(25)]
    samples = _iter_generated_comparisons(
        context=context,
        rows=rows,
        tokenizer=Tokenizer(),
        adapter_model=model,
        torch_module=SimpleNamespace(manual_seed=lambda seed: None, inference_mode=nullcontext),
    )
    if scorer == "specialist_decision":
        path, preview, wins = persist(tmp_path, samples, 25)
        completed = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(preview) == 20 and sum(wins.values()) == 25
        assert len({row["id"] for row in completed}) == 25
    else:
        completed = list(samples)
    assert len(completed) == count
    assert model.calls == count * 2
