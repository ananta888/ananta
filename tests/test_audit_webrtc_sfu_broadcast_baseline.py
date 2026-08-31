from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

AUDIT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/audit_webrtc_sfu_broadcast_baseline.py"
)

FIXTURE_FILES = {
    ".git/HEAD": f"{'a' * 40}\n",
    "agent/repositories/semantic_relay_shared_store.py": (
        "class SharedSemanticRelayRepository:\n    pass\n"
    ),
    "agent/routes/webrtc_signaling.py": "class Blueprint:\n    pass\n",
    "agent/services/semantic_sfu_admission_service.py": (
        "class SemanticSfuAdmissionService:\n    pass\n"
    ),
    "agent/services/sfu_broadcast_feature_policy.py": (
        "BROADCAST_FLAG = dict(key=\"semantic_media_broadcast\")\n"
    ),
    "agent/services/sfu_broadcast_participant_limits.py": (
        "SFU_BROADCAST_MAX_GROUP_MEMBERS = 8\n"
    ),
    "artifacts/test-gates/semantic-media-program-evidence.json": "{}\n",
    "config/livekit.semantic-media.yaml": "room:\n  max_participants: 250\n",
    "docker-compose.semantic-media.yml": (
        "services:\n  semantic-media-sfu:\n    image: fixture\n"
        "  semantic-media-turn-gate:\n    image: fixture\n"
    ),
    "docs/architecture/pair-view-sync.md": "# Pair view sync\n",
    "docs/operations/semantic-media-sfu.md": "# SFU operations\n",
    "docs/privacy/semantic-media-observability.md": "# Observability\n",
    "frontend-angular/package.json": (
        "{\n  \"dependencies\": {\n    \"livekit-client\": \"2.20.1\"\n  }\n}\n"
    ),
    "frontend-angular/src/app/services/livekit-sfu-room.adapter.ts": (
        "export const keyProvider = 'BaseKeyProvider';\n"
        "export const permissions = 'setTrackSubscriptionPermissions';\n"
    ),
    "frontend-angular/src/app/services/sfu-broadcast-limits.ts": (
        "export const SFU_BROADCAST_MAX_PUBLICATION_RECIPIENTS = 7;\n"
    ),
    "frontend-angular/src/app/services/sfu-media-frame-crypto.service.spec.ts": (
        "SfuMediaFrameCryptoService;\n"
    ),
    "frontend-angular/src/app/services/sfu-media-frame-crypto.service.ts": (
        "export class SfuMediaFrameCryptoService {}\n"
    ),
    "frontend-angular/src/app/services/webrtc-media-session.service.ts": (
        "export class WebrtcMediaSessionService {}\n"
    ),
    "migrations/versions/c7d8e9f0a1b2_add_semantic_media_contracts.py": "# fixture\n",
    "migrations/versions/e6f7a8b9c0d1_add_semantic_sfu_admission_state.py": "# fixture\n",
    "pyproject.toml": "[project]\nname = \"baseline-fixture\"\n",
    "requirements.txt": "",
    "schemas/webrtc/group_key_epoch.v1.json": (
        '{"title": "Ananta WebRTC Group Key Epoch Authorization v1"}\n'
    ),
    "schemas/webrtc/media_publication.v1.json": (
        '{"title": "Hub-authorized SFU media publication"}\n'
    ),
    "schemas/webrtc/media_subscription.v1.json": (
        '{"title": "Hub-authorized SFU media subscription"}\n'
    ),
    "tests/chaos/test_semantic_sfu_failover.py": "# fixture\n",
    "tests/e2e/test_semantic_sfu_spike.py": '{"receivers": 2}\n',
    "todos/active/todo.webrtc-sfu-broadcast-fanout.json": (
        "{\n"
        '  "activation_readiness": {\n'
        '    "parent_decision": "no_go",\n'
        '    "parent_rollout_stage": "observe_only",\n'
        '    "source_artifact": '
        '"artifacts/test-gates/semantic-media-program-evidence.json"\n'
        "  }\n"
        "}\n"
    ),
}


@pytest.fixture
def baseline_root(tmp_path: Path) -> Path:
    for relative_path, content in FIXTURE_FILES.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _run_audit(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_identical_fixture_tree_produces_identical_output(baseline_root: Path):
    first = _run_audit(baseline_root)
    second = _run_audit(baseline_root)

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["source_id_policy"] == (
        "repository_path_and_symbol_only; no_source_or_run_ids_synthesized"
    )
    assert "SRC_" not in first.stdout
    assert "RUN_" not in first.stdout


def test_missing_required_path_returns_nonzero(baseline_root: Path):
    missing_path = baseline_root / "config/livekit.semantic-media.yaml"
    missing_path.unlink()

    result = _run_audit(baseline_root)

    assert result.returncode != 0
    error = json.loads(result.stdout)
    assert error["reason_code"] == "webrtc_sfu_broadcast_baseline_invalid"
    assert error["detail"].startswith("missing_required_path:")
    assert error["detail"].endswith("/config/livekit.semantic-media.yaml")


def test_contradictory_status_claim_returns_nonzero(baseline_root: Path):
    (baseline_root / "requirements.txt").write_text("livekit\n", encoding="utf-8")

    result = _run_audit(baseline_root)

    assert result.returncode != 0
    error = json.loads(result.stdout)
    assert error == {
        "detail": (
            "contradictory_status_claim:livekit_server_sdk:"
            "requirements.txt:livekit"
        ),
        "reason_code": "webrtc_sfu_broadcast_baseline_invalid",
    }
