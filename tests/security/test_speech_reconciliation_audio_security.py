from __future__ import annotations

import hashlib
import io
import time
import wave
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationContractError,
    canonical_json,
)
from tests.speech_reconciliation_support import job_payload
from voice_runtime.preprocessing.audio_decode import AudioDecodeError, FfmpegAudioDecoder
from worker.runtime.speech_reconciliation_app import create_app
from worker.speech_reconciliation.audio_staging import (
    AesGcmSpeechArtifactDecryptor,
    EpochKeyring,
    SpeechAudioStager,
    SpeechAudioStagingError,
    derive_artifact_key,
)
from worker.speech_reconciliation.contracts import SpeechReconciliationWorkerTask

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "compose-next" / "compose.speech-reconciliation.yml"
DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.speech-reconciliation-worker"


class _Authority:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.phases: list[str] = []

    def verify(self, task, *, phase: str):
        del task
        self.phases.append(phase)
        return (self.active, None if self.active else "speech_reconciliation_consent_revoked")


class _RecordingDecryptor:
    def __init__(self, plaintext: bytes) -> None:
        self.plaintext = plaintext
        self.calls = 0

    def decrypt(self, task, ciphertext: bytes) -> bytes:
        del task, ciphertext
        self.calls += 1
        return self.plaintext


def _wav(duration_ms: int = 100) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\0\0" * (16_000 * duration_ms // 1000))
    return output.getvalue()


def _task_payload(
    plaintext: bytes,
    *,
    ciphertext: bytes,
    content_digest: str | None = None,
    key_epoch: int = 1,
    decoded_pcm_bytes: int = 64 * 1024,
    duration_ms: int = 1000,
) -> dict:
    zero = {
        "wall_time_ms": 0,
        "cpu_time_ms": 0,
        "gpu_time_ms": 0,
        "memory_byte_ms": 0,
        "disk_bytes": 0,
        "checkpoint_bytes": 0,
        "energy_millijoules": 0,
    }
    remaining = {
        **zero,
        "wall_time_ms": 60_000,
        "cpu_time_ms": 60_000,
        "disk_bytes": 16 * 1024 * 1024,
        "checkpoint_bytes": 1024 * 1024,
    }
    job = job_payload(
        deadline_at_ms=time.time_ns() // 1_000_000 + 60_000,
        source_duration_ms=10_000,
        key_epoch=key_epoch,
        stage="staging",
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "task_type": "speech_reconciliation_attempt",
        "job": job,
        "budget_ledger": {
            "contract_version": CONTRACT_VERSION,
            "job_id": job["job_id"],
            "attempt_id": job["attempt_id"],
            "fencing_epoch": job["fencing_epoch"],
            "sequence": job["ledger_sequence"],
            "stage": job["stage"],
            "source_duration_ms": job["source_duration_ms"],
            "compute_factor": job["max_compute_factor"],
            "allocated": remaining,
            "reserved": zero,
            "consumed": zero,
            "remaining": remaining,
        },
        "audio_artifact": {
            "artifact_ref": job["input_artifact_ref"],
            "transport_digest": hashlib.sha256(ciphertext).hexdigest(),
            "content_digest": content_digest or hashlib.sha256(plaintext).hexdigest(),
            "filename": "source.wav",
            "content_type": "audio/wav",
            "ciphertext_bytes": len(ciphertext),
            "plaintext_bytes": len(plaintext),
            "decoded_pcm_bytes": decoded_pcm_bytes,
            "duration_ms": duration_ms,
            "key_epoch": key_epoch,
        },
        "execution_plan": {
            "max_parallel_passes": 1,
            "pass_deadline_ms": 10_000,
            "passes": [
                {
                    "pass_id": "pass-a",
                    "model_id": "model-a",
                    "model_revision": "revision-a",
                    "variant_id": "original",
                    "language": "de",
                }
            ],
        },
    }


def _encrypted_task(plaintext: bytes, root_key: bytes):
    # AES-GCM adds a 16-byte tag and the worker envelope prefixes a 12-byte nonce.
    placeholder = b"x" * (len(plaintext) + 28)
    task = SpeechReconciliationWorkerTask.from_mapping(_task_payload(plaintext, ciphertext=placeholder))
    nonce = b"n" * 12
    key = derive_artifact_key(root_key, key_epoch=1, artifact_ref=task.audio_artifact.artifact_ref)
    ciphertext = nonce + AESGCM(key).encrypt(
        nonce,
        plaintext,
        canonical_json(task.audio_artifact.aad_mapping(task.job)),
    )
    return SpeechReconciliationWorkerTask.from_mapping(_task_payload(plaintext, ciphertext=ciphertext)), ciphertext


def test_staging_checks_authority_and_transport_digest_before_decryption(tmp_path: Path) -> None:
    wav = _wav()
    ciphertext = b"c" * (len(wav) + 28)
    task = SpeechReconciliationWorkerTask.from_mapping(_task_payload(wav, ciphertext=ciphertext))
    decryptor = _RecordingDecryptor(wav)
    authority = _Authority(active=False)
    stager = SpeechAudioStager(workspace_root=tmp_path / "audio", decryptor=decryptor, authority=authority)

    with pytest.raises(SpeechAudioStagingError) as denied:
        with stager.stage(task, ciphertext):
            pass
    assert denied.value.reason_code == "speech_reconciliation_consent_revoked"
    assert decryptor.calls == 0

    authority.active = True
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(SpeechAudioStagingError) as mismatch:
        with stager.stage(task, tampered):
            pass
    assert mismatch.value.reason_code == "speech_reconciliation_artifact_transport_tamper"
    assert decryptor.calls == 0


def test_aead_stage_is_private_and_removed_after_success_and_restart(tmp_path: Path) -> None:
    root_key = b"k" * 32
    task, ciphertext = _encrypted_task(_wav(), root_key)
    workspace_root = tmp_path / "audio"
    stager = SpeechAudioStager(
        workspace_root=workspace_root,
        decryptor=AesGcmSpeechArtifactDecryptor(EpochKeyring({1: root_key})),
        authority=_Authority(),
    )

    with stager.stage(task, ciphertext) as staged:
        assert staged.workspace_root.stat().st_mode & 0o777 == 0o700
        assert staged.encoded_path.stat().st_mode & 0o777 == 0o600
        assert staged.wav_path.is_file()
    assert list(workspace_root.iterdir()) == []

    stale = workspace_root / "speech-reconciliation-stale"
    stale.mkdir(mode=0o700)
    (stale / "audio.bin").write_bytes(b"plaintext-remnant")
    SpeechAudioStager(
        workspace_root=workspace_root,
        decryptor=AesGcmSpeechArtifactDecryptor(EpochKeyring({1: root_key})),
        authority=_Authority(),
    )
    assert not stale.exists()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"PK\x03\x04" + b"0" * 1024, "speech_reconciliation_audio_format_unsupported"),
        (_wav()[:20], "speech_reconciliation_decode_invalid_wav"),
    ],
)
def test_archive_bomb_and_truncated_audio_are_rejected_without_residue(
    tmp_path: Path,
    payload: bytes,
    expected: str,
) -> None:
    ciphertext = b"x" * (len(payload) + 28)
    task = SpeechReconciliationWorkerTask.from_mapping(
        _task_payload(payload, ciphertext=ciphertext, decoded_pcm_bytes=4096)
    )
    root = tmp_path / "audio"
    stager = SpeechAudioStager(
        workspace_root=root,
        decryptor=_RecordingDecryptor(payload),
        authority=_Authority(),
    )
    with pytest.raises(SpeechAudioStagingError) as error:
        with stager.stage(task, ciphertext):
            pass
    assert error.value.reason_code == expected
    assert list(root.iterdir()) == []


def test_symlink_root_cancel_and_decoder_timeout_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "audio"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(SpeechAudioStagingError) as boundary:
        SpeechAudioStager(workspace_root=root, decryptor=_RecordingDecryptor(_wav()), authority=_Authority())
    assert boundary.value.reason_code == "speech_reconciliation_workspace_boundary_violation"

    wav = _wav()
    ciphertext = b"x" * (len(wav) + 28)
    task = SpeechReconciliationWorkerTask.from_mapping(_task_payload(wav, ciphertext=ciphertext))
    decryptor = _RecordingDecryptor(wav)
    clean_root = tmp_path / "clean"
    stager = SpeechAudioStager(workspace_root=clean_root, decryptor=decryptor, authority=_Authority())

    def cancelled() -> None:
        raise RuntimeError("cancel")

    with pytest.raises(SpeechAudioStagingError) as cancel:
        with stager.stage(task, ciphertext, cancellation_check=cancelled):
            pass
    assert cancel.value.reason_code == "speech_reconciliation_cancelled"
    assert decryptor.calls == 0

    class _TimeoutDecoder:
        def decode(self, *, filename: str, payload: bytes):
            del filename, payload
            raise TimeoutError("private decoder content must not escape")

    timed = SpeechAudioStager(
        workspace_root=clean_root,
        decryptor=decryptor,
        authority=_Authority(),
        decoder_factory=lambda _limits: _TimeoutDecoder(),
    )
    with pytest.raises(SpeechAudioStagingError) as timeout:
        with timed.stage(task, ciphertext):
            pass
    assert timeout.value.reason_code == "speech_reconciliation_decoder_timeout"
    assert "private" not in str(timeout.value)
    assert list(clean_root.iterdir()) == []


def test_tampered_aead_and_malicious_codec_errors_never_leak_decoder_stderr(tmp_path: Path) -> None:
    root_key = b"a" * 32
    task, ciphertext = _encrypted_task(_wav(), root_key)
    modified = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    modified_task = SpeechReconciliationWorkerTask.from_mapping(_task_payload(_wav(), ciphertext=modified))
    stager = SpeechAudioStager(
        workspace_root=tmp_path / "audio",
        decryptor=AesGcmSpeechArtifactDecryptor(EpochKeyring({1: root_key})),
        authority=_Authority(),
    )
    with pytest.raises(SpeechAudioStagingError) as authentication:
        with stager.stage(modified_task, modified):
            pass
    assert authentication.value.reason_code == "speech_reconciliation_artifact_authentication_failed"

    class _MaliciousCodecDecoder:
        def decode(self, *, filename: str, payload: bytes):
            del filename, payload
            raise AudioDecodeError("decode.ffmpeg_failed", "SECRET transcript in stderr")

    plain_task = SpeechReconciliationWorkerTask.from_mapping(
        _task_payload(_wav(), ciphertext=b"q" * (len(_wav()) + 28))
    )
    malicious = SpeechAudioStager(
        workspace_root=tmp_path / "malicious",
        decryptor=_RecordingDecryptor(_wav()),
        authority=_Authority(),
        decoder_factory=lambda _limits: _MaliciousCodecDecoder(),
    )
    with pytest.raises(SpeechAudioStagingError) as codec:
        with malicious.stage(plain_task, b"q" * (len(_wav()) + 28)):
            pass
    assert codec.value.reason_code == "speech_reconciliation_decode_ffmpeg_failed"
    assert "SECRET" not in str(codec.value)

    argv = FfmpegAudioDecoder().build_argv(detected_format="mp3", binary="/usr/bin/ffmpeg")
    blacklist = argv[argv.index("-protocol_blacklist") + 1]
    assert all(protocol in blacklist.split(",") for protocol in ("file", "http", "https", "tcp", "udp"))
    assert argv[argv.index("-threads") + 1] == "1"


def test_worker_task_rejects_stale_key_epoch_and_underbudget_before_runtime() -> None:
    wav = _wav()
    ciphertext = b"x" * (len(wav) + 28)
    stale = _task_payload(wav, ciphertext=ciphertext)
    stale["audio_artifact"]["key_epoch"] = 2
    with pytest.raises(SpeechReconciliationContractError) as epoch:
        SpeechReconciliationWorkerTask.from_mapping(stale)
    assert epoch.value.reason_code == "speech_reconciliation_audio_binding_mismatch"

    underbudget = _task_payload(wav, ciphertext=ciphertext)
    underbudget["budget_ledger"]["allocated"]["disk_bytes"] = 1
    underbudget["budget_ledger"]["remaining"]["disk_bytes"] = 1
    with pytest.raises(SpeechReconciliationContractError) as budget:
        SpeechReconciliationWorkerTask.from_mapping(underbudget)
    assert budget.value.reason_code == "speech_reconciliation_stage_disk_budget_exceeded"


def test_compose_and_image_enforce_isolation_resource_and_network_boundaries() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    assert compose["networks"]["speech-reconciliation-control"]["internal"] is True
    for name in ("speech-reconciliation-worker-cpu", "speech-reconciliation-worker-nvidia"):
        service = services[name]
        assert service["read_only"] is True
        assert service["user"] == "10008:10008"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["pids_limit"] == 128
        assert set(service["networks"]) == {"speech-reconciliation-control"}
        assert "ports" not in service and "extra_hosts" not in service
        assert service["cpus"] and service["mem_limit"]
        assert all("size=" in mount and "noexec" in mount for mount in service["tmpfs"])
        assert service["volumes"][0]["read_only"] is True
        assert service["volumes"][0]["bind"]["create_host_path"] is False
        environment = service["environment"]
        assert environment["HF_HUB_OFFLINE"] == "1"
        assert environment["TRANSFORMERS_OFFLINE"] == "1"
        assert environment["VOICE_ALLOW_MODEL_DOWNLOAD"] == "false"
        assert not any("HUB_URL" in key for key in environment)
    devices = services["speech-reconciliation-worker-nvidia"]["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "USER 10008:10008" in dockerfile
    assert "COPY --chown=10008:10008 agent" not in dockerfile
    assert "worker/runtime/lora_training_app.py" not in dockerfile
    assert "worker/training" not in dockerfile


def test_http_artifact_transfer_requires_worker_auth_version_and_octet_stream() -> None:
    wav = _wav()
    ciphertext = b"x" * (len(wav) + 28)
    task_payload = _task_payload(wav, ciphertext=ciphertext)

    class _Runtime:
        def health(self):
            return {"status": "ok"}

        def readiness(self):
            return {"ready": True}

        def submit(self, task):
            return {"job_id": task.job.job_id, "status": "awaiting_audio"}

        def upload_audio(self, job_id, payload):
            return {"job_id": job_id, "status": "accepted", "bytes": len(payload)}

        def status(self, job_id):
            return {"job_id": job_id, "status": "running"}

        def cancel(self, job_id, *, attempt_id, fencing_token_digest):
            del attempt_id, fencing_token_digest
            return {"job_id": job_id, "status": "cancel_requested"}

        def drain(self):
            return {"status": "draining"}

    token = "speech-reconciliation-test-token-123"
    client = create_app(runtime=_Runtime(), auth_token=token).test_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Ananta-Contract-Version": CONTRACT_VERSION,
    }
    assert client.post("/internal/v1/speech-reconciliation/jobs", json=task_payload).status_code == 401
    wrong = {**headers, "X-Ananta-Contract-Version": "ananta.lora-training.v1"}
    response = client.post("/internal/v1/speech-reconciliation/jobs", json=task_payload, headers=wrong)
    assert response.status_code == 422
    assert response.get_json()["error"]["reason_code"] == "speech_reconciliation_contract_version_invalid"
    assert client.post("/internal/v1/speech-reconciliation/jobs", json=task_payload, headers=headers).status_code == 202
    wrong_mime = client.put(
        f"/internal/v1/speech-reconciliation/jobs/{task_payload['job']['job_id']}/audio",
        data=ciphertext,
        headers=headers,
        content_type="audio/wav",
    )
    assert wrong_mime.status_code == 422
    accepted = client.put(
        f"/internal/v1/speech-reconciliation/jobs/{task_payload['job']['job_id']}/audio",
        data=ciphertext,
        headers=headers,
        content_type="application/octet-stream",
    )
    assert accepted.status_code == 202
