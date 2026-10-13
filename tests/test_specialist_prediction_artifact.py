"""Hub dataset binding for synthetic worker artifacts, without trusting worker scores."""

import hashlib
import io
import json

import pytest

from agent.services.specialist_prediction_artifact import (
    SpecialistPredictionArtifactError,
    read_specialist_predictions,
)
from worker.training.specialist_predictions import persist_specialist_predictions


def fixture_rows():
    records = [{"instruction": f"case {i}", "output": '{"label":"deny"}'} for i in range(25)]
    rows = []
    for index, record in enumerate(records):
        identity = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        rows.append(
            {
                "id": identity,
                "record_index": index,
                "base_output": "invalid",
                "adapter_output": '{"label":"allow"}',
                "expected_output": record["output"],
                "base_score": {"total": 1},
                "adapter_score": {"total": 1},
                "winner": "adapter",
            }
        )
    return records, rows


def read(data, records, **overrides):
    arguments = {
        "admitted_sha256": hashlib.sha256(data).hexdigest(),
        "admitted_size_bytes": len(data),
        "expected_records": records,
        **overrides,
    }
    return read_specialist_predictions(io.BytesIO(data), **arguments)


def encode(rows):
    return b"".join((json.dumps(row) + "\n").encode() for row in rows)


def test_real_worker_writer_artifact_binds_all_rows_and_returns_raw_wrong_labels(tmp_path):
    records, rows = fixture_rows()
    path, preview, _wins = persist_specialist_predictions(
        tmp_path, iter(rows), expected_records=25, check_cancelled=lambda: None
    )
    outputs = read(path.read_bytes(), records)
    assert len(preview) == 20 and len(outputs.adapter) == 25
    assert outputs.adapter == tuple('{"label":"allow"}' for _ in range(25))
    assert outputs.base == tuple("invalid" for _ in range(25))


@pytest.mark.parametrize("mutation", ["missing", "extra", "order", "identity", "reference", "duplicate", "field"])
def test_even_correctly_hashed_artifact_cannot_replace_admitted_dataset(mutation):
    records, rows = fixture_rows()
    if mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append(dict(rows[-1]))
    elif mutation == "order":
        rows.reverse()
    elif mutation == "identity":
        rows[0]["id"] = "a" * 64
    elif mutation == "reference":
        rows[0]["expected_output"] = '{"label":"allow"}'
    elif mutation == "duplicate":
        rows[1]["id"] = rows[0]["id"]
    else:
        rows[0]["shell"] = "unrequested"
    with pytest.raises(SpecialistPredictionArtifactError):
        read(encode(rows), records)


def test_mutated_raw_outputs_fail_original_hub_admitted_hash():
    records, rows = fixture_rows()
    original = encode(rows)
    rows[0]["adapter_output"] = '{"label":"deny"}'
    changed = encode(rows)
    with pytest.raises(SpecialistPredictionArtifactError):
        read(changed, records, admitted_sha256=hashlib.sha256(original).hexdigest())


@pytest.mark.parametrize("suffix", [b" ", b"{}\n"])
def test_trailing_bytes_are_not_ignored(suffix):
    records, rows = fixture_rows()
    with pytest.raises(SpecialistPredictionArtifactError):
        read(encode(rows) + suffix, records)


def test_duplicate_json_fields_are_rejected():
    records, rows = fixture_rows()
    data = encode(rows).replace(b'"record_index": 0', b'"record_index": 9, "record_index": 0', 1)
    with pytest.raises(SpecialistPredictionArtifactError):
        read(data, records)
