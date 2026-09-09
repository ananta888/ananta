"""Actual packaged visual Worker and private Meet; synthetic owner/source only."""

import json
import os
import threading
import time

import pytest

pytestmark = [
    pytest.mark.timeout(240),
    pytest.mark.skipif(
        os.environ.get("MEET_VISUAL_PACKAGED_GATE") != "1", reason="explicit private visual container gate"
    ),
]


@pytest.mark.parametrize("source", ["camera", "screen"])
def test_real_packaged_visual_source_is_grant_control_and_worker_bound(
    app, tmp_path, monkeypatch, record_property, source
):
    from tests.meet_dialog_browser_fixture import docker
    from tests.meet_receive_packaged_fixture import packaged_receive

    with packaged_receive(app, tmp_path, monkeypatch, source=source, image=os.environ["MEET_VISUAL_WORKER_IMAGE"]) as f:
        service, tasks, principal, container, command = f.service, f.tasks, f.principal, f.container, f.command
        accepted, completed = [], threading.Event()
        native = service.visual_result

        def observe(payload):
            result = native(payload)
            # Observe only already validated closed statistics, no raw media.
            accepted.append((payload["visual_task_id"], payload["result"]))
            completed.set()
            return result

        monkeypatch.setattr(service, "visual_result", observe)
        exchanges = {}
        native_exchange = service.exchange

        def observe_exchange(payload):
            result = native_exchange(payload)
            exchanges.update(
                publications=len(result["authorization"]["publications"]),
                job_present=result.get("visual_job") is not None,
                visual_enabled=result["controls"]["visual"]["enabled"],
            )
            return result

        monkeypatch.setattr(service, "exchange", observe_exchange)

        def diagnostic():
            raw = docker(
                "exec",
                container.name,
                "python",
                "-c",
                "from pathlib import Path; p=Path('/state/dialog-diagnostic.json'); "
                "print(p.read_text() if p.exists() else '{}')",
            )
            with app.app_context():
                row = tasks.get_by_id(started["task_id"])
                state = row.worker_execution_context.get("meet_visual", {})
                job = state.get("job")
                status = tasks.get_by_id(job["task_id"]).status if job else None
            return json.dumps(
                {
                    "worker": json.loads(raw),
                    "parent_status": row.status,
                    "child_status": status,
                    "reservations": state.get("count", 0),
                    "exchange": exchanges,
                }
            )

        started = f.start({})
        assert command("source") == {"source_started": True}
        assert command("grant") == {"source_granted": True}
        with app.app_context():
            parent = tasks.get_by_id(started["task_id"])
            assert "meet_visual" not in parent.worker_execution_context
            service.control(
                principal,
                "synthetic",
                parent.id,
                {
                    "expected_revision": 1,
                    "chat": False,
                    "audio": False,
                    "screen": False,
                    "visual": True,
                },
            )
        assert completed.wait(30), "bounded visual completion missing: " + diagnostic()
        assert len(accepted) == 1
        child_id, features = accepted[0]
        assert features["profile"] == "image-features-v1" and len(features["frames"]) == 3
        channel = 0 if source == "camera" else 1
        assert all(frame["average_rgb"][channel] > 150 for frame in features["frames"])
        assert all(frame["width"] <= 640 and frame["height"] <= 360 for frame in features["frames"])
        assert command("revoke") == {"source_revoked": True}
        with app.app_context():
            child = tasks.get_by_id(child_id)
            assert child.status == "completed" and child.parent_task_id == started["task_id"]
            assert child.assigned_agent_url == container.origin
            assert "average_rgb" not in str(child.worker_execution_context)
            assert "jpegBase64" not in str(child.worker_execution_context)
        delegated, next_jobs = threading.Event(), []
        native_start = service.visual

        def observe_start(payload):
            result = native_start(payload)
            next_jobs.append(result["job"]["task_id"])
            delegated.set()
            return result

        monkeypatch.setattr(service, "visual", observe_start)
        assert command("grant") == {"source_granted": True}
        assert delegated.wait(35), "bounded second visual assignment missing: " + diagnostic()
        assert command("revoke") == {"source_revoked": True}
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with app.app_context():
                revoked = tasks.get_by_id(next_jobs[0])
            if revoked.status == "failed":
                break
            time.sleep(0.1)
        assert revoked.status == "failed" and len(accepted) == 1, "revoked source must not complete"
        record_property(
            "packaged_visual",
            {
                "source": source,
                "frames": 3,
                "synthetic_source": True,
                "actual_native_child": True,
                "active_source_revocation": True,
                "production_release_evidence": False,
            },
        )
