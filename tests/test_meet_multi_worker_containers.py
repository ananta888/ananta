"""Single-host Hub/two packaged Workers/Meet gate; synthetic policy, real owned screens."""

import base64
import hashlib
import json
import os
import selectors
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.timeout(360),
    pytest.mark.skipif(
        os.environ.get("MEET_MULTI_WORKER_GATE") != "1", reason="explicit two-Worker-container private Meet gate"
    ),
]


@pytest.mark.parametrize(
    "media_mode", [False, True, "browser"], ids=["screen-only", "persona-speech", "browser-workspaces"]
)
def test_two_role_assigned_packaged_workers_share_owned_screens_and_stop_independently(
    app, tmp_path, monkeypatch, record_property, media_mode
):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    from werkzeug.serving import WSGIRequestHandler, make_server

    from agent.bootstrap.meet_dialog_diagnostics import configure_dialog_diagnostics
    from agent.database import engine
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.repositories.meet_role_assignment import SqlMeetRoleAssignments
    from agent.services.meet_authorization_client import MeetAuthorizationClient
    from agent.services.meet_contract import MeetError
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_publishers import MeetDialogPublishers
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.source_control_access_policy import HubSourcePrincipal
    from tests.meet_companion_build import require_current_browser_build
    from tests.meet_dialog_browser_fixture import docker
    from tests.meet_dialog_cleanup import cancel_fixture_dialog
    from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
    from tests.meet_dialog_worker_container import DialogWorkerContainer
    from tests.meet_multi_role_fixture import PARENTS, seed_multi_role_parents
    from tests.meet_multi_worker_browser import MultiWorkerBrowserScenario
    from tests.meet_multi_worker_media import MultiWorkerMediaScenario
    from tests.test_meet_dialog_cross_repository import close_bridge

    assert os.environ.get("ANANTA_TEST_DATABASE_MODE") == "wal", "isolated concurrent SQL profile required"
    meet = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    require_current_browser_build(meet, os.environ.get("MEET_TEST_PUBLIC_DIR"))
    key = Ed25519PrivateKey.generate()
    private, public, worker_key = (tmp_path / name for name in ("hub.pem", "hub-public.pem", "worker-key"))
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    hmac_key = b"synthetic-two-worker-test-key-material"
    worker_key.write_bytes(hmac_key)
    worker_key.chmod(0o600)
    principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
    media = MultiWorkerMediaScenario() if media_mode is True else None
    browser = MultiWorkerBrowserScenario(media_mode == "browser")
    capabilities = media.capabilities if media is not None else ["screen.publish"]
    duration_seconds = media.start_options["duration_seconds"] if media is not None else 120
    with ExitStack() as cleanup:
        bridge = subprocess.Popen(
            ["node", "test/helpers/machine-multi-hub-bridge.mjs"],
            cwd=meet,
            env=os.environ
            | {
                "MEET_TEST_HUB_PUBLIC_KEY": str(public),
                "MEET_MULTI_WORKER_MEDIA_GATE": "1" if media is not None else "0",
            },
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        cleanup.callback(close_bridge, bridge)

        def receive(timeout=25):
            with selectors.DefaultSelector() as selector:
                selector.register(bridge.stdout, selectors.EVENT_READ)
                assert selector.select(timeout), "bounded two-Worker bridge response missing"
                line = bridge.stdout.readline(4097)
            assert line and len(line) <= 4096, "two-Worker bridge closed or oversized"
            return json.loads(line)

        def command(name, **fields):
            bridge.stdin.write(json.dumps({"command": name, **fields}) + "\n")
            bridge.stdin.flush()
            response = receive()
            assert "bridge_error" not in response, json.dumps(
                {
                    "bridge": response,
                    "workers": worker_diagnostics(),
                    "exchange_failures": exchange_failures,
                    "reply_count": len(media.worker.calls) if media is not None else 0,
                }
            )
            return response

        def worker_diagnostics():
            # Read only the test seam's bounded enum/line projection, never logs,
            # browser contents, arbitrary exception messages or assignment inputs.
            records = []
            for container in containers:
                output = docker(
                    "exec",
                    container.name,
                    "python",
                    "-c",
                    "from pathlib import Path; p=Path('/state/dialog-diagnostic.json'); "
                    "print(p.read_text() if p.exists() else '{}')",
                )
                records.append(json.loads(output))
            return records

        ready = receive(60)
        assert set(ready) == {"origin", "room_id", "certificate", "test_network"}, ready
        monkeypatch.setenv("SSL_CERT_FILE", ready["certificate"])
        certificate = x509.load_pem_x509_certificate(Path(ready["certificate"]).read_bytes())
        spki = base64.b64encode(
            hashlib.sha256(
                certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            ).digest()
        ).decode()
        network = json.loads(docker("network", "inspect", ready["test_network"]))[0]
        assert network["Internal"] is True and len(network["IPAM"]["Config"]) == 1
        gateway = network["IPAM"]["Config"][0]["Gateway"]

        class Quiet(WSGIRequestHandler):
            def log(self, *_args, **_kwargs):
                pass

        hub = make_server(gateway, 0, app, threaded=True, request_handler=Quiet)
        cleanup.callback(hub.server_close)
        cleanup.callback(hub.shutdown)
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        hub_url = f"http://{gateway}:{hub.server_port}/api/meet/v1/internal/dialog"
        containers = [
            DialogWorkerContainer(
                ready["test_network"],
                os.environ["MEET_MULTI_WORKER_IMAGE"],
                hub_url,
                lifetime=300,
                diagnostics=True,
                browser_documents=browser.enabled,
            )
            for _ in range(2)
        ]
        for container in containers:
            cleanup.callback(container.close)
            container.start(worker_key, ready["certificate"], spki)
            state = json.loads(docker("inspect", container.name))[0]
            assert state["HostConfig"]["ReadonlyRootfs"] is True
            assert state["Config"]["User"] == "1000:1000"
            assert not state["HostConfig"].get("Devices") and not state["HostConfig"].get("DeviceRequests")
            assert {m["Destination"] for m in state["Mounts"] if m["Type"] == "bind"} == {
                "/test/sitecustomize.py",
                "/test/worker-key",
                "/test/meet-ca.pem",
            }
            docker(
                "exec",
                container.name,
                "python",
                "-c",
                "import importlib.util; assert importlib.util.find_spec('agent') is None",
            )
        origins = [c.origin for c in containers]
        assert origins[0] != origins[1]
        seed_multi_role_parents(engine, publishers=origins, tenant="synthetic", project="synthetic")
        transports = {origin: HttpMediaWorker(origin + "/v1/turns", hmac_key) for origin in origins}
        binding = SyntheticMeetBinding(ready["origin"], ready["room_id"], principal)
        tasks = HubDialogTasks(
            publisher_url=origins[0],
            organization_principals=True,
            publishers=MeetDialogPublishers(SqlMeetRoleAssignments(), origins, origins[0]),
        )
        authority = MeetDialogAuthority(tasks, binding, {("synthetic", "synthetic"): capabilities})
        preauthorization = None
        if os.environ.get("MEET_MULTI_WORKER_PREAUTHORIZATION_GATE") == "1":
            from tests.meet_multi_worker_preauthorization import MultiWorkerPreauthorization

            preauthorization = MultiWorkerPreauthorization(
                engine, authority, PARENTS, principal, ready["room_id"], duration_seconds=duration_seconds
            )
        issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", private)
        reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
        reservations.initialize()
        dispatches.initialize()
        service = MeetDialogService(
            authority,
            tasks,
            MeetAuthorizationClient(authority, issuer),
            issuer,
            MeetDialogWorkerRouter(authority, tasks, transports, origins[0]),
            transports[origins[0]],
            reservations,
            dispatches,
            **(media.service_options(binding, dispatches) if media is not None else {}),
            **browser.service_options(authority, tasks),
        )
        exchange_failures = []
        exchange_states = {}
        native_exchange = service.exchange

        def observe_exchange(payload):
            began = time.monotonic()
            try:
                result = native_exchange(payload)
                exchange_states[payload["task_id"]] = {
                    "began": began,
                    "receive_revision": result["authorization"]["receiveRevision"],
                    "control_revision": result["controls"]["chat"]["revision"],
                    "chat_enabled": result["controls"]["chat"]["enabled"],
                    "read_grants": sum(g["chatRead"] is True for g in result["authorization"]["grants"]),
                }
                return result
            except MeetError as error:
                if len(exchange_failures) < 8:
                    allowed = {
                        "meet_authorization_unavailable",
                        "meet_authorization_scope_invalid",
                        "meet_authorization_state_invalid",
                        "meet_authorization_contract_invalid",
                        "meet_authorization_changed",
                        "meet_dialog_lifecycle_unavailable",
                        "meet_dialog_task_inactive",
                        "meet_dialog_organization_inactive",
                    }
                    exchange_failures.append(
                        {"code": error.code if error.code in allowed else "redacted", "status": error.status}
                    )
                raise

        monkeypatch.setattr(service, "exchange", observe_exchange)
        app.config["ROLE"] = "hub"
        app.extensions.update(meet_binding_service=binding, meet_dialog_service=service, meet_media_worker_key=hmac_key)
        configure_dialog_diagnostics(app, engine, binding)
        diagnostics = app.extensions["meet_dialog_diagnostics"]
        started, subjects = [], []

        def wait_chat_ready(index):
            since = time.monotonic()
            while time.monotonic() < since + 8:
                state = exchange_states.get(started[index]["task_id"])
                if state is not None and state["began"] >= since:
                    expected = {"open": True, **{k: state[k] for k in ("control_revision", "receive_revision")}}
                    if containers[index].chat_state() == expected:
                        return
                time.sleep(0.1)
            with app.app_context():
                statuses = [tasks.get_by_id(row["task_id"]).status for row in started]
            raise AssertionError(
                json.dumps(
                    {
                        "chat_ready_missing": index,
                        "latest_exchange": state,
                        "marker": containers[index].chat_state(),
                        "task_statuses": statuses,
                        "workers": worker_diagnostics(),
                        "exchange_failures": exchange_failures,
                    }
                )
            )

        for index, parent in enumerate(PARENTS):
            with app.app_context():
                result = service.start(
                    principal,
                    "synthetic",
                    {
                        "capabilities": capabilities,
                        "duration_seconds": duration_seconds,
                        "chat_mode": "off",
                        **(media.initial_options(index) if media is not None else {}),
                        **browser.start_options,
                    },
                    parent=parent,
                )
                started.append(result)
                cleanup.callback(cancel_fixture_dialog, app, service, principal, result["task_id"])
                task = tasks.get_by_id(result["task_id"])
                if preauthorization is not None:
                    preauthorization.require_bound(task, len(started) - 1)
                subjects.append(task.worker_execution_context["meet_machine_principal"]["subject"])
        admitted = command("bind", subjects=subjects)
        with app.app_context():
            statuses = [tasks.get_by_id(row["task_id"]).status for row in started]
        assert admitted == {
            "matchedPrincipals": 2,
            "distinctDevices": True,
            "participants": 3,
        }, json.dumps(
            {
                "admission": admitted,
                "task_statuses": statuses,
                "workers": worker_diagnostics(),
                "exchange_failures": exchange_failures,
            }
        )
        both = command("screens")
        with app.app_context():
            statuses = [tasks.get_by_id(row["task_id"]).status for row in started]
        assert both == {"moving": [True, True], "departedAbsent": False}, json.dumps(
            {
                "screens": both,
                "task_statuses": statuses,
                "workers": worker_diagnostics(),
                "exchange_failures": exchange_failures,
            }
        )
        if media is not None:
            media.exercise(app, service, principal, started, command, record_property, wait_chat_ready)
        browser.exercise(app, principal, started, containers, command, record_property)
        cancel_fixture_dialog(app, service, principal, started[0]["task_id"])
        browser.after_departure(app, principal, started[1], containers[1], record_property)
        survivor = command("survivor")
        assert survivor == {"moving": [False, True], "departedAbsent": True}, survivor
        if media is not None:
            media.survivor(command)
        with app.app_context():
            assert tasks.get_by_id(started[0]["task_id"]).status == "cancelled"
            assert tasks.get_by_id(started[1]["task_id"]).status == "in_progress"
        revoke_started = time.monotonic()
        if preauthorization is not None:
            assert preauthorization.revoke(1)["status"] == "revoked"
        else:
            cancel_fixture_dialog(app, service, principal, started[1]["task_id"])
        assert command("alone") == {"alone": True, "captures": 0, "transformErrors": 0, "connectionDrops": 0}
        if preauthorization is not None:
            revoked_ms = (time.monotonic() - revoke_started) * 1000
            assert revoked_ms < 5000, "end-to-end operator-revocation budget exceeded"
            record_property("operator_preauthorization_revocation_ms", revoked_ms)
        expected_statuses = ["cancelled", "failed" if preauthorization is not None else "cancelled"]
        until = time.monotonic() + 8
        terminal_tasks = []
        # Receiver departure precedes the ordinary finish callback by design.
        # Compare immutable snapshots only after the actual terminal transition.
        while time.monotonic() < until:
            with app.app_context():
                terminal_tasks = [tasks.get_by_id(row["task_id"]).model_dump() for row in started]
            if [row["status"] for row in terminal_tasks] == expected_statuses:
                break
            time.sleep(0.05)
        assert [row["status"] for row in terminal_tasks] == expected_statuses, "bounded terminal Task finish missing"
        observations = []
        while time.monotonic() < until:
            with app.app_context():
                observations = [diagnostics.inspect(principal, "synthetic", row["task_id"]) for row in started]
            if all(row["observation_status"] == "recorded" for row in observations):
                break
            time.sleep(0.05)
        assert all(row["observation_status"] == "recorded" for row in observations), "bounded terminal reports missing"
        assert all(row["classification"] == "unverified_worker_observation" for row in observations)
        assert [row["hub_task_status"] for row in observations] == expected_statuses
        assert all(row["observation"]["measurements"]["elapsed_ms"] > 0 for row in observations)
        with app.app_context():
            assert [tasks.get_by_id(row["task_id"]).model_dump() for row in started] == terminal_tasks
            browser.require_terminal(tasks)
        record_property("unverified_terminal_worker_observations", observations)
        record_property(
            "two_packaged_worker_screens",
            {
                "single_host": True,
                "synthetic_policy": True,
                "production_release_evidence": False,
                "worker_containers": 2,
                "image": containers[0].image,
                "worker_source_mounts": False,
                "distinct_role_principals": True,
                "both": both,
                "survivor": survivor,
            },
        )
