from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "a" * 64


def validate(schema_name: str, value: dict) -> None:
    schema = json.loads((ROOT / f"schemas/webrtc/{schema_name}").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(value)


def frame() -> dict:
    return {
        "schema": "ananta.semantic-frame.v1", "frame_id": "frame", "session_id": "session",
        "contract_id": "contract", "contract_digest": DIGEST, "lease_id": "lease", "lease_digest": DIGEST,
        "epoch": 1, "sequence": 2, "frame_kind": "reference", "base_reference_id": None,
        "scene_digest": DIGEST, "algorithm": {"name": "standard-web-codec", "version": "1.0.0", "codec": "image/webp"},
        "encoded_digest": DIGEST, "total_bytes": 4, "created_at_ms": 1000, "expires_at_ms": 2000,
    }


def test_semantic_frame_and_residual_are_closed_bounded_standard_codec_contracts() -> None:
    value = frame()
    validate("semantic_frame.v1.json", value)
    chunk = {
        "schema": "ananta.visual-residual-chunk.v1", "chunk_id": "chunk", "session_id": "session",
        "contract_id": "contract", "lease_id": "lease", "epoch": 1, "sequence": 2,
        "frame_digest": DIGEST, "index": 0, "total_chunks": 1, "chunk_bytes": 4, "total_bytes": 4,
        "codec": "image/webp", "expires_at_ms": 2000, "data": "AQIDBA==",
    }
    validate("visual_residual_chunk.v1.json", chunk)
    for target in (value, chunk):
        forged = copy.deepcopy(target)
        forged["raw_pixels"] = [1]
        schema = "semantic_frame.v1.json" if target is value else "visual_residual_chunk.v1.json"
        with pytest.raises(jsonschema.ValidationError):
            validate(schema, forged)


def test_reconstruction_and_validator_reports_reject_content_fields() -> None:
    reconstruction = {
        "schema": "ananta.reconstruction-report.v1", "report_id": "report", "session_id": "session",
        "receiver_id": "receiver", "contract_id": "contract", "contract_digest": DIGEST,
        "lease_id": "lease", "lease_digest": DIGEST, "lease_expires_at_ms": 2000,
        "epoch": 1, "sequence": 2, "observed_at_ms": 1000, "source": "local_measurement",
        "stage_ms": {"encode": 1, "transport": 2, "reassembly": 1, "render": 3, "recovery": 0},
        "resources": {"cpu_ms": 3, "gpu_ms": 0, "working_bytes": 100},
        "bytes": {"encoded": 4, "transported": 8}, "delay_ms": 2000,
        "quality": {"score": 0.9, "drift": 0.01, "stale_regions": 0},
        "input_digest": DIGEST, "output_digest": DIGEST,
    }
    validate("reconstruction_report.v1.json", reconstruction)
    validator = {
        "schema": "ananta.semantic-validator-report.v1", "report_id": "validator-report", "session_id": "session",
        "contract_id": "contract", "validator_lease_id": "lease", "validator_id": "validator",
        "validator_role": "validator", "audience": "hub", "epoch": 1, "sequence": 2,
        "input_digest": DIGEST, "output_digest": DIGEST,
        "criteria": {
            "schema_valid": True, "binding_valid": True, "quality_score": 0.9,
            "drift_score": 0.01, "deadline_met": True,
        },
        "verdict": "pass", "observed_at_ms": 1000, "expires_at_ms": 2000,
        "signature": "signed-validator-report-0001",
    }
    validate("semantic_validator_report.v1.json", validator)
    for schema_name, value in (
        ("reconstruction_report.v1.json", reconstruction),
        ("semantic_validator_report.v1.json", validator),
    ):
        leaked = copy.deepcopy(value)
        leaked["frame"] = "raw"
        with pytest.raises(jsonschema.ValidationError):
            validate(schema_name, leaked)
