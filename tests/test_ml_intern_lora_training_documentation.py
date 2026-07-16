from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from agent.services.ml_intern_training_contract import CreateTrainingJobCommand

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/operations/lora-training.md"


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _bash_blocks(document: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", document, flags=re.DOTALL)


def test_lora_runbook_local_links_and_shell_examples_are_valid() -> None:
    document = _runbook()
    local_links = [
        target.split("#", maxsplit=1)[0]
        for target in re.findall(r"\[[^]]+]\(([^)]+)\)", document)
        if not target.startswith(("#", "http://", "https://"))
    ]

    assert local_links
    assert all((RUNBOOK.parent / target).resolve().is_file() for target in local_links)
    for block in _bash_blocks(document):
        syntax = subprocess.run(
            ["bash", "-n"],
            input=block,
            text=True,
            check=False,
            capture_output=True,
        )
        assert syntax.returncode == 0, syntax.stderr


def test_lora_runbook_live_payload_matches_the_hub_contract() -> None:
    document = _runbook()
    live_block = next(block for block in _bash_blocks(document) if "/api/ml-intern-training/jobs" in block)
    encoded_payload = re.search(r"-d '(\{.*\})'", live_block, flags=re.DOTALL)

    assert encoded_payload is not None
    payload = json.loads(encoded_payload.group(1))
    command = CreateTrainingJobCommand.from_mapping(payload)
    assert command.mode == "live"
    assert command.request_spec["live_confirmed"] is True
    assert 8 <= len(command.request_spec["risk_reason"]) <= 500


def test_lora_runbook_documents_validation_hash_and_runtime_cas_contracts() -> None:
    document = _runbook()

    assert "GET /api/ml-intern-training/datasets/{dataset_id}/validation-report" in document
    assert "http://hub:5000/api/ml-intern-training/datasets/lora-dataset-123/validation-report" in document
    assert "_tree_sha256(Path(sys.argv[1]))" in document
    assert "_tree_sha256(Path(sys.argv[1]).resolve" not in document
    assert "Runtime-`rollback` unterstützt mit `expected_version`" in document
    assert "`unload`" in document
    assert "keine CAS-Garantie" in document
