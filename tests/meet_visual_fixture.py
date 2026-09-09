"""Admitted synthetic project and real SQL Tasks; no external credentials or media."""

from sqlmodel import Session, select

from tests.test_meet_dialog_avatar_negotiation import system


def visual_system(*, active=True):
    from agent.database import engine
    from agent.db_models import ProjectDB

    with Session(engine) as session:
        if (
            session.exec(
                select(ProjectDB).where(ProjectDB.tenant_id == "tenant", ProjectDB.project_id == "project")
            ).first()
            is None
        ):
            session.add(
                ProjectDB(
                    tenant_id="tenant", project_id="project", name="Synthetic visual", created_by_subject_id="owner"
                )
            )
            session.commit()
    f = system(profiles=False)
    f.f.authority.policies[("tenant", "project")] |= {"video.receive"}
    started = f.service.start(f.principal, "project", f.payload | {"capabilities": ["video.receive"]})
    f.task_id = started["task_id"]
    f.assignment = f.worker.start_dialog.call_args.args[0]
    f.ids = tuple(f.assignment[k] for k in ("task_id", "lease_id", "runtime_id"))
    f.scope = f.f.authority.current(*f.ids)
    f.receipt = {
        "lease": {"sessionId": "ms_" + "a" * 32, "generation": 1, "expiresAt": (f.f.now + 120) * 1000},
        "peerId": "b" * 16,
        "roomId": f.scope.room_id,
        "membershipEpoch": 2,
        "receiveRevision": 3,
        "grants": [
            {
                "publisherPeerId": "c" * 16,
                "machinePeerId": "b" * 16,
                "publicationIds": ["camera"],
                "chatRead": False,
                "expiresAt": (f.f.now + 120) * 1000,
            }
        ],
        "publications": [{"peerId": "c" * 16, "publicationId": "camera", "source": "camera", "publicationEpoch": 4}],
    }
    f.meet.inspect.return_value = f.receipt
    f.payload = {k: f.assignment[k] for k in ("task_id", "lease_id", "runtime_id")}
    f.payload |= {"nonce": "d" * 32, "meet_session_id": f.receipt["lease"]["sessionId"]}
    if active:
        f.service.control(
            f.principal,
            "project",
            f.task_id,
            {"expected_revision": 1, "chat": False, "audio": False, "screen": False, "visual": True},
        )
        f.scope = f.f.authority.current(*f.ids)
    return f
