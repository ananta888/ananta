"""Native Hub composition over the private persistent DB; synthetic policy only."""

from pathlib import Path


def create_restart_hub(config):
    from sqlmodel import Session

    from agent.ai_agent import create_app
    from agent.database import engine, init_db
    from agent.db_models import TaskDB
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
    from agent.services.background.meet_dialog_deadlines import start_meet_dialog_deadlines
    from agent.services.meet_authorization_client import MeetAuthorizationClient
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_deadlines import MeetDialogDeadlines
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.source_control_access_policy import HubSourcePrincipal
    from agent.services.task_runtime_service import compare_and_set_local_task_status
    from tests.meet_dialog_lifecycle_fixture import seed_parent
    from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
    from tests.meet_restart_hub_control import register_control

    init_db()
    app = create_app(agent="synthetic-restart-hub", testing=True)
    principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
    origin = config["worker_origin"]
    with app.app_context(), Session(engine) as session:
        if session.get(TaskDB, "meet-test-parent") is None:
            seed_parent(engine, tenant="synthetic", project="synthetic", publisher=origin)
    binding = SyntheticMeetBinding(config["meeting_origin"], config["room_id"], principal)
    tasks = HubDialogTasks(publisher_url=origin)
    authority = MeetDialogAuthority(tasks, binding, {("synthetic", "synthetic"): ["video.receive"]})
    issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", "/test/hub.pem")
    worker_key = Path("/test/worker-key").read_bytes()
    transport = HttpMediaWorker(origin + "/v1/turns", worker_key)
    reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
    reservations.initialize()
    dispatches.initialize()
    service = MeetDialogService(
        authority,
        tasks,
        MeetAuthorizationClient(authority, issuer),
        issuer,
        MeetDialogWorkerRouter(authority, tasks, {origin: transport}, origin),
        transport,
        reservations,
        dispatches,
    )
    app.config.update(ROLE="hub", TESTING=False)
    app.extensions.update(meet_binding_service=binding, meet_dialog_service=service, meet_media_worker_key=worker_key)
    app.extensions["meet_dialog_deadlines"] = MeetDialogDeadlines(
        SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status)
    )
    register_control(app, service, principal, Path("/test/control-key").read_bytes())
    # Start exactly the normal deadline service, not unrelated autonomous loops.
    start_meet_dialog_deadlines(app)
    return app
