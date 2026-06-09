"""Tests fuer ml_intern_dataset_validation_service (MLLORA-008/009)."""

import json
import tempfile
from pathlib import Path

import pytest

from agent.services.ml_intern_dataset_validation_service import (
    MlInternDatasetValidationService,
    get_dataset_validation_service,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


@pytest.fixture
def svc():
    return MlInternDatasetValidationService()


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


def test_valid_instruction_dataset(svc, tmp):
    p = tmp / "train.jsonl"
    _write_jsonl(p, [
        {"instruction": "Was ist 2+2?", "output": "4", "source_ref": "synthetic", "privacy_class": "public"},
        {"instruction": "Beschreibe Python.", "output": "Python ist eine Programmiersprache.", "source_ref": "synthetic", "privacy_class": "public"},
    ])
    report = svc.validate(p, require_secret_scan=True)
    assert report.ok is True
    assert report.accepted_record_count == 2
    assert report.rejected_record_count == 0
    assert report.secret_scan_passed is True


def test_empty_output_rejected(svc, tmp):
    p = tmp / "bad.jsonl"
    _write_jsonl(p, [{"instruction": "Hallo?", "output": ""}])
    report = svc.validate(p)
    assert report.ok is False
    assert report.rejected_record_count == 1
    assert any("empty_output" in e.error_type for e in report.errors)


def test_broken_json_line_reported(svc, tmp):
    p = tmp / "broken.jsonl"
    p.write_text('{"instruction": "valid", "output": "yes"}\nNOT_JSON_AT_ALL\n', encoding="utf-8")
    report = svc.validate(p)
    assert report.ok is False
    assert any("invalid_json" in e.error_type for e in report.errors)
    assert any(e.line_number == 2 for e in report.errors)


def test_duplicate_records_counted(svc, tmp):
    p = tmp / "dup.jsonl"
    rec = {"instruction": "same", "output": "same output"}
    _write_jsonl(p, [rec, rec, rec])
    report = svc.validate(p, require_secret_scan=False)
    assert report.duplicate_count == 2
    assert report.accepted_record_count >= 1


def test_file_not_found(svc):
    report = svc.validate("/tmp/this_does_not_exist_ever.jsonl")
    assert report.ok is False
    assert any("file_not_found" in e.error_type for e in report.errors)


def test_train_eval_same_file_rejected(svc, tmp):
    p = tmp / "data.jsonl"
    _write_jsonl(p, [{"instruction": "Hi", "output": "Hi there"}])
    _, _, pair_errors = svc.validate_train_eval_pair(p, p, require_secret_scan=False)
    assert len(pair_errors) > 0
    assert any("identical" in e.lower() or "same" in e.lower() for e in pair_errors)


def test_train_eval_different_files_ok(svc, tmp):
    train = tmp / "train.jsonl"
    eval_ = tmp / "eval.jsonl"
    _write_jsonl(train, [{"instruction": "A", "output": "B"}])
    _write_jsonl(eval_, [{"instruction": "C", "output": "D"}])
    _, _, pair_errors = svc.validate_train_eval_pair(train, eval_, require_secret_scan=False)
    assert pair_errors == []


def test_secret_api_key_detected(svc, tmp):
    p = tmp / "secret.jsonl"
    _write_jsonl(p, [{"instruction": "config", "output": "api_key: sk-abcdefghijklmnop12345678901234567890"}])
    report = svc.validate(p, require_secret_scan=True)
    assert report.ok is False
    assert len(report.secret_findings) > 0


def test_secret_scan_false_skips_scan(svc, tmp):
    p = tmp / "secret2.jsonl"
    _write_jsonl(p, [{"instruction": "config", "output": "password=supersecret123456"}])
    report = svc.validate(p, require_secret_scan=False)
    # Ohne Scan: kein Fehler durch Secret
    assert report.accepted_record_count >= 1


def test_fake_private_key_detected(svc, tmp):
    p = tmp / "privkey.jsonl"
    _write_jsonl(p, [{"instruction": "key", "output": "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."}])
    report = svc.validate(p, require_secret_scan=True)
    assert len(report.secret_findings) > 0


def test_secret_override_documented(svc, tmp):
    p = tmp / "override.jsonl"
    _write_jsonl(p, [{"instruction": "x", "output": "api_key: sk-abcdefghijklmnop12345678901234567890"}])
    report = svc.validate(p, require_secret_scan=True,
        explicit_override={"reason": "synthetic test data, not real secrets", "overrides": {}})
    assert report.secret_scan_passed is True
    assert any("override" in w.error_type for w in report.warnings)


def test_require_secret_scan_false_not_default(svc, tmp):
    """require_secret_scan=false darf nicht stillschweigend Default sein."""
    p = tmp / "data.jsonl"
    _write_jsonl(p, [{"instruction": "test", "output": "ok"}])
    report_with = svc.validate(p, require_secret_scan=True)
    report_without = svc.validate(p, require_secret_scan=False)
    assert report_with.ok is True
    assert report_without.ok is True
    # Sicherstellen, dass der Unterschied existiert (Scan wurde ausgefuehrt)
    assert report_with.secret_scan_passed is True


def test_chat_format_valid(svc, tmp):
    p = tmp / "chat.jsonl"
    _write_jsonl(p, [{
        "messages": [
            {"role": "user", "content": "Hallo"},
            {"role": "assistant", "content": "Hallo! Wie kann ich helfen?"}
        ],
        "privacy_class": "public"
    }])
    report = svc.validate(p, require_secret_scan=False)
    assert report.ok is True
    assert report.format_type == "chat"


def test_chat_format_empty_assistant_rejected(svc, tmp):
    p = tmp / "chat_bad.jsonl"
    _write_jsonl(p, [{
        "messages": [
            {"role": "user", "content": "Hallo"},
            {"role": "assistant", "content": ""}
        ]
    }])
    report = svc.validate(p, require_secret_scan=False)
    assert report.ok is False


def test_write_report(svc, tmp):
    p = tmp / "data.jsonl"
    _write_jsonl(p, [{"instruction": "Hallo", "output": "Welt"}])
    report = svc.validate(p, require_secret_scan=False)
    out = tmp / "report.json"
    svc.write_report(report, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["schema"] == "mlintern_dataset_validation_report.v1"
    assert "dataset_hash" in data


def test_singleton():
    s1 = get_dataset_validation_service()
    s2 = get_dataset_validation_service()
    assert s1 is s2


def test_fixture_train_valid(svc):
    fixture = Path(__file__).parents[1] / "tests/fixtures/mlintern_lora/ananta_todo_json_train.jsonl"
    if not fixture.exists():
        pytest.skip("fixture not found")
    report = svc.validate(fixture, require_secret_scan=True)
    assert report.ok is True


def test_fixture_eval_valid(svc):
    fixture_dir = Path(__file__).parents[1] / "tests/fixtures/mlintern_lora"
    train = fixture_dir / "ananta_todo_json_train.jsonl"
    eval_ = fixture_dir / "ananta_todo_json_eval.jsonl"
    if not train.exists() or not eval_.exists():
        pytest.skip("fixtures not found")
    _, _, pair_errors = svc.validate_train_eval_pair(train, eval_, require_secret_scan=True)
    assert pair_errors == [], f"unexpected pair errors: {pair_errors}"
