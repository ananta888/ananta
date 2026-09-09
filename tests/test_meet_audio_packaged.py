"""Actual Meet PCM -> packaged local CUDA ASR; synthetic speech, no capture."""

import base64
import json
import os
import threading
import time

import pytest

from ananta_contracts.meet_audio_profile import AudioReceiveProfile
from tests.meet_dialog_browser_fixture import docker
from tests.meet_receive_packaged_fixture import packaged_receive

pytestmark = [
    pytest.mark.timeout(240),
    pytest.mark.skipif(os.environ.get("MEET_AUDIO_PACKAGED_GATE") != "1", reason="explicit live receive CUDA gate"),
]


@pytest.mark.parametrize("source", ["microphone", "screen-audio"])
def test_packaged_cuda_transcribes_only_granted_live_source_without_persisting_text(
    app, tmp_path, monkeypatch, record_property, source
):
    image = os.environ["MEET_AUDIO_WORKER_IMAGE"]
    with packaged_receive(app, tmp_path, monkeypatch, source=source, image=image) as f:
        # A fresh file owned only by this container; source bytes cross only into
        # the private synthetic publisher, never into a Hub task or production UI.
        raw = docker(
            "exec",
            f.container.name,
            "timeout",
            "25",
            "python",
            "-c",
            "import base64,tempfile; from pathlib import Path; from worker.meet_media.speech import speech; "
            "d=tempfile.TemporaryDirectory(prefix='meet-test-source-'); p=Path(d.name)/'speech.wav'; "
            "speech('Hallo, dies ist ein Test für die lokale Spracherkennung.',p,max_seconds=8); "
            "print(base64.b64encode(p.read_bytes()).decode()); d.cleanup()",
        )
        assert len(raw) <= 540000
        wav = base64.b64decode(raw.splitlines()[-1], validate=True)
        assert 44 < len(wav) <= 400000 and wav[:4] == b"RIFF"
        f.wav.write_bytes(wav)
        del wav, raw
        accepted, completed = [], threading.Event()
        native = f.service.transcript

        def observe(payload):
            result = native(payload)
            text = payload["text"].casefold()
            accepted.append(
                {
                    "child": payload["audio_task_id"],
                    "samples": payload["end_sample"],
                    "language": payload["language"],
                    "reply": result["reply"],
                    "matched_words": sum(word in text for word in ("hallo", "test", "lokale", "spracherkennung")),
                    "word_matches": [word in text for word in ("hallo", "test", "lokale", "spracherkennung")],
                }
            )
            completed.set()
            return result

        monkeypatch.setattr(f.service, "transcript", observe)
        delegated, jobs, admission_errors, exchange_state = threading.Event(), [], [], {}
        native_start = f.service.audio

        def observe_start(payload):
            try:
                result = native_start(payload)
            except Exception as error:
                if len(admission_errors) < 8:
                    code = getattr(error, "code", None)
                    admission_errors.append(
                        code
                        if code
                        in {
                            "meet_audio_job_busy_or_exhausted",
                            "meet_audio_task_inactive",
                            "meet_audio_policy_denied",
                        }
                        else "redacted"
                    )
                raise
            jobs.append(result["job"])
            delegated.set()
            return result

        monkeypatch.setattr(f.service, "audio", observe_start)
        renewed = threading.Event()
        native_exchange = f.service.exchange

        def observe_exchange(payload):
            result = native_exchange(payload)
            exchange_state.update(
                publications=len(result["authorization"]["publications"]),
                job_present=result["audio_job"] is not None,
                audio_enabled=result["controls"]["audio"]["enabled"],
            )
            if result["authorization"]["lease"]["generation"] >= 2:
                renewed.set()
            return result

        monkeypatch.setattr(f.service, "exchange", observe_exchange)

        def diagnostic():
            raw = docker(
                "exec",
                f.container.name,
                "python",
                "-c",
                "from pathlib import Path; p=Path('/state/dialog-diagnostic.json'); "
                "print(p.read_text() if p.exists() else '{}')",
            )
            with app.app_context():
                parent = f.tasks.get_by_id(started["task_id"])
                context = parent.worker_execution_context["meet_dialog"]
                job = context.get("audio_job")
                child_status = f.tasks.get_by_id(job["task_id"]).status if job else None
            return json.dumps(
                {
                    "worker": json.loads(raw),
                    "parent": parent.status,
                    "child": child_status,
                    "count": context.get("audio_count"),
                    "exchange": exchange_state,
                    "admission_errors": admission_errors,
                }
            )

        profile = AudioReceiveProfile(segment_seconds=8).projection()
        started = f.start(
            {
                "audio_mode": "transcribe",
                "audio_profile": profile,
                "duration_seconds": 180 if source == "microphone" else 120,
            }
        )
        assert f.command("source") == {"source_started": True}
        with app.app_context():
            parent = f.tasks.get_by_id(started["task_id"])
            assert parent.worker_execution_context["meet_dialog"].get("audio_job") is None
        assert f.command("grant") == {"source_granted": True}
        assert delegated.wait(15), "bounded audio assignment missing"
        assert f.command("speak") == {"speech_started": True}
        if not completed.wait(40):
            raw = docker(
                "exec",
                f.container.name,
                "python",
                "-c",
                "from pathlib import Path; p=Path('/state/dialog-diagnostic.json'); "
                "print(p.read_text() if p.exists() else '{}')",
            )
            with app.app_context():
                parent = f.tasks.get_by_id(started["task_id"])
                job = parent.worker_execution_context["meet_dialog"].get("audio_job")
                status = f.tasks.get_by_id(job["task_id"]).status if job else None
            pytest.fail(
                json.dumps(
                    {
                        "transcript_missing": True,
                        "worker": json.loads(raw),
                        "parent_status": parent.status,
                        "audio_status": status,
                    }
                )
            )
        assert len(accepted) == 1
        result = accepted[0]
        assert result["samples"] == 128000 and result["language"] == "de"
        assert result["reply"] is None and result["matched_words"] >= 3, result["word_matches"]
        assert f.command("revoke") == {"source_revoked": True}
        with app.app_context():
            child = f.tasks.get_by_id(result["child"])
            assert child.status == "completed" and child.parent_task_id == started["task_id"]
            assert child.assigned_agent_url == f.container.origin
            context = json.dumps(child.worker_execution_context).casefold()
            assert not any(word in context for word in ("spracherkennung", "pcm", "wav", "transcript"))
        def await_original_reservation(job):
            delay = max(0, job["deadline"] - time.time() + 0.1)
            assert delay <= 30.2, "unexpected Hub reservation duration"
            threading.Event().wait(delay)

        # A completed result does not release the Hub's original admission
        # budget. Start a fresh utterance after that budget, not during silence
        # after an utterance that has already ended before re-admission is legal.
        await_original_reservation(jobs[0])
        delegated.clear()
        assert f.command("grant") == {"source_granted": True}
        assert f.command("speak") == {"speech_started": True}
        assert delegated.wait(40), "bounded audio readmission missing: " + diagnostic()
        assert len(jobs) == 2
        assert f.command("revoke") == {"source_revoked": True}

        def require_failed(job):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with app.app_context():
                    revoked = f.tasks.get_by_id(job["task_id"])
                if revoked.status == "failed":
                    break
                time.sleep(0.1)
            assert revoked.status == "failed" and len(accepted) == 1

        require_failed(jobs[1])
        if source == "microphone":
            assert renewed.wait(80), "actual Meet lease renewal missing"
            await_original_reservation(jobs[1])
            delegated.clear()
            assert f.command("grant") == {"source_granted": True}
            assert f.command("speak") == {"speech_started": True}
            assert delegated.wait(15), "new-generation audio assignment missing"
            assert len(jobs) == 3 and jobs[2]["generation"] > jobs[0]["generation"]
            with app.app_context():
                current = f.service.inspect(f.principal, "synthetic", started["task_id"])["controls"]
                f.service.control(
                    f.principal,
                    "synthetic",
                    started["task_id"],
                    {
                        "expected_revision": current["revision"],
                        "chat": False,
                        "audio": False,
                        "screen": False,
                    },
                )
            require_failed(jobs[2])
        with app.app_context():
            assert f.tasks.get_by_id(started["task_id"]).status == "in_progress"
        record_property(
            "packaged_audio_receive",
            {
                "source": source,
                "samples": result["samples"],
                "matched_words": result["matched_words"],
                "actual_cuda": True,
                "readmission_and_active_revoke": True,
                "renewed_generation_and_pause": source == "microphone",
                "synthetic_source": True,
                "production_release_evidence": False,
            },
        )
