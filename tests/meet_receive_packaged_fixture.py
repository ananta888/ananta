"""Owned private receive infrastructure; separate from media scenario assertions."""

import os
import threading
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace


@contextmanager
def packaged_receive(app, tmp_path, monkeypatch, *, source, image):
    from werkzeug.serving import WSGIRequestHandler, make_server

    from agent.database import engine
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.services.meet_authorization_client import MeetAuthorizationClient
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.source_control_access_policy import HubSourcePrincipal
    from tests.meet_dialog_cleanup import cancel_fixture_dialog
    from tests.meet_dialog_lifecycle_fixture import seed_parent
    from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
    from tests.meet_dialog_worker_container import DialogWorkerContainer
    from tests.meet_receive_infrastructure import private_receive

    assert os.environ.get("ANANTA_TEST_DATABASE_MODE") == "wal"
    principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
    with private_receive(tmp_path, source=source) as wire, ExitStack() as cleanup:
        ready, gateway, capability = wire.ready, wire.gateway, wire.capability
        monkeypatch.setenv("SSL_CERT_FILE", ready["certificate"])

        class Quiet(WSGIRequestHandler):
            def log(self, *_args, **_kwargs):
                pass

        hub = make_server(gateway, 0, app, threaded=True, request_handler=Quiet)
        cleanup.callback(hub.server_close)
        cleanup.callback(hub.shutdown)
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        container = DialogWorkerContainer(
            ready["test_network"],
            image,
            f"http://{gateway}:{hub.server_port}/api/meet/v1/internal/dialog",
            lifetime=240,
            diagnostics=True,
            gpu=wire.gpu,
        )
        cleanup.callback(container.close)
        container.start(wire.worker_key, ready["certificate"], wire.spki)
        seed_parent(engine, tenant="synthetic", project="synthetic", publisher=container.origin)
        binding = SyntheticMeetBinding(ready["origin"], ready["room_id"], principal)
        tasks = HubDialogTasks(publisher_url=container.origin)
        authority = MeetDialogAuthority(tasks, binding, {("synthetic", "synthetic"): [capability]})
        issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", wire.private)
        transport = HttpMediaWorker(container.origin + "/v1/turns", wire.hmac_key)
        reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
        reservations.initialize()
        dispatches.initialize()
        service = MeetDialogService(
            authority,
            tasks,
            MeetAuthorizationClient(authority, issuer),
            issuer,
            MeetDialogWorkerRouter(authority, tasks, {container.origin: transport}, container.origin),
            transport,
            reservations,
            dispatches,
        )
        app.config["ROLE"] = "hub"
        app.extensions.update(
            meet_binding_service=binding, meet_dialog_service=service, meet_media_worker_key=wire.hmac_key
        )

        def start(options):
            with app.app_context():
                result = service.start(
                    principal,
                    "synthetic",
                    {
                        "capabilities": [capability],
                        "duration_seconds": 120,
                        "chat_mode": "off",
                        **options,
                    },
                    parent="meet-test-parent",
                )
            cleanup.callback(cancel_fixture_dialog, app, service, principal, result["task_id"])
            return result

        yield SimpleNamespace(
            service=service,
            tasks=tasks,
            principal=principal,
            container=container,
            command=wire.command,
            start=start,
            wav=wire.wav,
        )
