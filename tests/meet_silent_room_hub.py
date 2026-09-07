"""Scoped synthetic policy with real Hub SQL/task/CAS and signed Worker HTTP."""

import threading
from contextlib import ExitStack
from unittest.mock import Mock

from sqlmodel import Session
from werkzeug.serving import WSGIRequestHandler, make_server

from agent.database import engine
from agent.db_models.projects import ProjectDB
from agent.repositories.meet_bindings import SqlMeetingStore
from agent.repositories.meet_chat_dispatches import SqlChatDispatches
from agent.repositories.meet_chat_reservations import SqlChatReservations
from agent.services.meet_authorization_client import MeetAuthorizationClient
from agent.services.meet_binding_service import MeetBindingService
from agent.services.meet_contract import MeetError, MeetProfile
from agent.services.meet_dialog_authority import MeetDialogAuthority
from agent.services.meet_dialog_service import MeetDialogService
from agent.services.meet_dialog_tasks import HubDialogTasks
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from agent.services.meet_media_transport import HttpMediaWorker
from agent.services.meet_room_allocation import MeetRoomAllocation
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.meet_dialog_cleanup import close_dialog_servers
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_runtime import run
from worker.meet_media.server import create_server


class _Quiet(WSGIRequestHandler):
    def log(self, *_args, **_kwargs):
        pass


class _Execution:
    def __init__(self):
        self.thread = None
        self.finished = threading.Event()
        self.failures = []

    def start(self, assignment):
        if self.thread is not None:
            raise ValueError("silent_duplicate_dispatch")

        def execute():
            hub = None
            status = "failed"
            try:
                hub = HubDialogClient(assignment)
                run(assignment, hub)
                status = "completed"
            except Exception as error:
                self.failures.append(
                    str(error) if str(error) == "meet_dialog_hub_revoked_or_unavailable" else type(error).__name__
                )
            finally:
                try:
                    if hub is not None:
                        hub.call("finish", status=status)
                except ValueError:
                    pass
                self.finished.set()

        self.thread = threading.Thread(target=execute, daemon=True)
        self.thread.start()
        return {
            "schema": "ananta.meet-dialog-accepted.v1",
            "status": "accepted",
            **{k: assignment[k] for k in ("task_id", "lease_id", "runtime_id")},
        }


class SilentRoomHub:
    def __init__(self, app, origin, private_key, worker_key, monkeypatch):
        self.app, self.started = app, None
        self.servers, self.threads = [], []
        self.execution = _Execution()
        self.principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
        self.cleanup = ExitStack()
        try:
            self._compose(app, origin, private_key, worker_key, monkeypatch)
        except BaseException:
            self.cleanup.close()
            raise

    def _compose(self, app, origin, private_key, worker_key, monkeypatch):
        with app.app_context(), Session(engine) as database:
            database.merge(
                ProjectDB(
                    tenant_id="synthetic",
                    project_id="synthetic",
                    name="Synthetic silent room",
                    created_by_subject_id="owner",
                )
            )
            database.commit()

        def require(**scope):
            if (scope["tenant_id"], scope["project_id"], scope["subject_id"]) != ("synthetic", "synthetic", "owner"):
                raise MeetError("synthetic_scope_denied", 403)

        def task_access(*_):
            raise MeetError("synthetic_parent_task_unavailable", 403)

        store = SqlMeetingStore(engine, origin)
        store.initialize()
        self.binding = MeetBindingService(MeetProfile(origin), store, Mock(require=require), task_access)
        self.allocation = MeetRoomAllocation(
            self.binding, {("synthetic", "synthetic")}, invite=self.binding.profile.invite
        )
        self.tasks = HubDialogTasks()
        authority = MeetDialogAuthority(self.tasks, self.binding, {("synthetic", "synthetic"): ["chat.send"]})
        issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", private_key)
        reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
        reservations.initialize()
        dispatches.initialize()
        self.media = Mock()
        self.media.execute.side_effect = AssertionError("silent_media_execution_forbidden")
        worker = create_server(("127.0.0.1", 0), worker_key, self.media, self.execution)
        self.servers.append(worker)
        self.cleanup.callback(worker.server_close)
        transport = HttpMediaWorker(f"http://127.0.0.1:{worker.server_port}/v1/turns", worker_key)
        self.service = MeetDialogService(
            authority,
            self.tasks,
            MeetAuthorizationClient(authority, issuer),
            issuer,
            transport,
            transport,
            reservations,
            dispatches,
        )
        self.exchanges = 0
        exchange = self.service.exchange

        def observed_exchange(payload):
            result = exchange(payload)
            self.exchanges += 1  # Count only successfully validated Hub responses.
            return result

        self.service.exchange = observed_exchange
        monkeypatch.setitem(app.config, "ROLE", "hub")
        for key, value in {
            "meet_binding_service": self.binding,
            "meet_dialog_service": self.service,
            "meet_media_worker_key": worker_key,
        }.items():
            monkeypatch.setitem(app.extensions, key, value)
        hub = make_server("127.0.0.1", 0, app, threaded=True, request_handler=_Quiet)
        self.servers.append(hub)
        self.cleanup.callback(hub.server_close)
        monkeypatch.setenv("MEET_HUB_DIALOG_URL", f"http://127.0.0.1:{hub.server_port}/api/meet/v1/internal/dialog")
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.threads.append(thread)
            thread.start()
            self.cleanup.callback(thread.join, timeout=2)
            self.cleanup.callback(server.shutdown)

    def allocate(self):
        with self.app.app_context():
            revision = self.binding.read(self.principal, "synthetic")["revision"]
            return self.allocation.allocate(self.principal, "synthetic", "", {"expected_revision": revision})

    def start(self):
        with self.app.app_context():
            self.started = self.service.start(
                self.principal,
                "synthetic",
                {"capabilities": ["chat.send"], "duration_seconds": 90, "chat_mode": "off", "audio_mode": "off"},
            )

    def stop(self):
        with self.app.app_context():
            return self.service.inspect(self.principal, "synthetic", self.started["task_id"], stop=True)

    def close(self):
        try:
            close_dialog_servers(self.app, self.service, self.principal, self.started, self.execution.thread, [])
        finally:
            self.cleanup.close()
