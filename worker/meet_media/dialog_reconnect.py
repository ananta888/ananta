"""Execute one Hub-admitted recovery with fresh control pulses and no media."""

import time

from ananta_contracts.meet_reconnect import RECOVERY_MS
from worker.meet_media.dialog_control_exchange import DialogControlExchange


def reconnect_session(
    assignment,
    hub,
    page,
    session,
    old_session,
    *,
    progress=None,
    clock=time.monotonic,
    wall_clock=time.time,
    exchange_factory=DialogControlExchange,
):
    # Only the signed client's closed recovery gate validates/advances attempts.
    # This executor cannot issue grants, reassign tasks or extend their lifetime.
    started = clock()
    state = hub.call("reconnect", meet_session_id=old_session, attempt=0)
    deadline = min(hub.deadline, started + RECOVERY_MS / 1000, clock() + (state["deadline_ms"] / 1000 - wall_clock()))
    attempt = state["attempt"]
    exchange = exchange_factory(
        hub,
        old_session,
        clock=clock,
        read=lambda: hub.call("reconnect", meet_session_id=old_session, attempt=attempt),
    )
    fresh_until = min(deadline, clock() + 2.5)
    meeting = state["meeting"]

    def require_current():
        nonlocal fresh_until, meeting
        now = clock()
        if now >= deadline or now >= fresh_until:
            raise ValueError("meet_dialog_reconnect_stale")
        update = exchange.poll()
        if update is not None:
            fresh_until = min(deadline, clock() + 2.5)
            if update["meeting"] is not None:
                if meeting is not None:
                    raise ValueError("meet_dialog_reconnect_grant_repeated")
                meeting = update["meeting"]
            if progress is not None:
                progress.report(fresh_until, hub.deadline)
        if clock() >= deadline or clock() >= fresh_until:
            raise ValueError("meet_dialog_reconnect_stale")

    try:
        require_current()
        if progress is not None:
            progress.report(fresh_until, hub.deadline)
        while meeting is None:
            require_current()
            page.wait_for_timeout(min(100, max(0, (min(deadline, fresh_until) - clock()) * 1000)))
        session.ready(
            assignment["capabilities"],
            avatar_videos=assignment.get("avatar_videos") is True,
            require_current=require_current,
        )
        session.join(meeting["room_id"], meeting["grant"], require_current=require_current)
        require_current()
    finally:
        exchange.close()
