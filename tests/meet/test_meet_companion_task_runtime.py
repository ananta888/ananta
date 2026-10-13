"""Owner-thread browser adapter fences publication while chat remains independent."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet.test_meet_companion_task_controls import fixture
from worker.meet_media.companion_task_controls import CompanionControlLost, CompanionSourceInterrupted
from worker.meet_media.companion_task_runtime import AssignedCompanionSession


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("kind", ["hub", "membership"])
def test_planned_deadline_ends_normally_but_earlier_authority_loss_propagates(expired, kind):
    from worker.meet_media.companion_chat_pump import CompanionSessionEnded
    from worker.meet_media.companion_task_runtime import _run_until_deadline

    now = [99]
    error = CompanionControlLost if kind == "hub" else CompanionSessionEnded

    def tick():
        now[0] = 100 if expired else 99
        raise error("synthetic_closed_execution")

    session, sleep = SimpleNamespace(tick=Mock(side_effect=tick)), Mock()
    if expired:
        _run_until_deadline(session, 100, clock=lambda: now[0], sleep=sleep)
    else:
        with pytest.raises(error):
            _run_until_deadline(session, 100, clock=lambda: now[0], sleep=sleep)
    session.tick.assert_called_once_with()
    sleep.assert_not_called()


def runtime():
    _now, controls, _state, _pool = fixture()
    page, model, media = Mock(), Mock(), Mock()
    client = SimpleNamespace(assignment={"session_id": "synthetic-session"})
    session = AssignedCompanionSession(page, client, model, media, controls, log=Mock())
    session.pump = Mock()
    session.pump.step.return_value = {"joined": True}
    return session, page, controls, media


@pytest.mark.parametrize("paused", [None, "avatar", "speech"])
def test_owner_tracks_speaking_generations_restores_idle_and_keeps_sources_independent(paused):
    from tests.meet.test_meet_snake_avatar_sync import FakeClock, fake_encoder, fake_ports, pcm_bytes, tone
    from worker.meet_media.companion_media import SpeechAvatarPublisher

    session, page, controls, media = runtime()
    pcm = pcm_bytes(tone(.3))
    ports, _events = fake_ports(FakeClock())
    clips = SpeechAvatarPublisher(ports, encoder=fake_encoder).prepare(pcm)[1]
    media.speaking.return_value = clips
    media.idle.return_value = clips[0] | {"repeatMode": "loop"}
    session.avatar_generation = 7
    session.avatar_revision = controls.state["controls"]["avatar"]
    generations, offsets, closes = [], [], []

    def browser(expression, argument=None):
        if "avatar.open" in expression:
            generation = 20 + len(generations)
            generations.append((generation, argument[1]["repeatMode"]))
            return {"generation": generation}
        if "avatar.close" in expression:
            closes.append(argument)
        if "speech.open" in expression:
            return {"generation": 30}
        if "speech.status" in expression:
            return {"bufferedSamples": 0}
        if "speech.push" in expression:
            offsets.append(argument[1])
            if len(offsets) == 3 and paused:
                controls.state["controls"][paused] = {"enabled": False, "revision": 2, "since": 2000}
        return None

    page.evaluate.side_effect = browser
    try:
        if paused == "speech":
            with pytest.raises(CompanionSourceInterrupted):
                session.speech(pcm, cooperate=session.step)
            assert len(offsets) == 3
        else:
            session.speech(pcm, cooperate=session.step)
            assert offsets == list(range(0, len(pcm) // 2, 441))
        assert generations[0] == (20, "hold_last")
        assert 7 in closes and 20 in closes
        if paused == "avatar":
            assert session.avatar_generation is None and len(generations) == 1
            media.idle.assert_not_called()
        else:
            assert generations[-1] == (21, "loop") and session.avatar_generation == 21
            media.idle.assert_called_once()
    finally:
        controls.close()


@pytest.mark.parametrize("expiry,renewed", [(120_000, True), (300_000, False)])
def test_renewal_reads_current_hub_grant_but_does_not_extend_past_task_deadline(expiry, renewed):
    from worker.meet_media.dialog_membership_loss import LOCAL_MEMBERSHIP

    session, page, controls, _media = runtime()
    session.client.assignment["deadline"] = 300
    session.client.read = Mock(return_value={"grant": "synthetic-grant"})
    page.evaluate.side_effect = lambda expression, *_args: (
        {"state": "closed"} if "speech.status" in expression else {"joined": True, "lease": {"expiresAt": expiry}})
    try:
        session.renew()
        session.client.read.assert_called_once_with()
        assert page.evaluate.call_count == (3 if renewed else 1)
        assert page.evaluate.call_args_list[0].args == (LOCAL_MEMBERSHIP,)
        if renewed:
            page.evaluate.assert_called_with("(g) => window.anantaMachine.renew(g)", "synthetic-grant")
    finally:
        controls.close()


@pytest.mark.parametrize("state", ["starting", "open"])
def test_active_speech_defers_native_renewal_without_skipping_hub_validation(state):
    session, page, controls, _media = runtime()
    session.client.assignment["deadline"] = 300
    session.client.read = Mock(return_value={"grant": "synthetic-grant"})
    page.evaluate.side_effect = lambda expression, *_args: (
        {"state": state} if "speech.status" in expression else {"joined": True, "lease": {"expiresAt": 120_000}})
    try:
        assert session.renew() is False
        session.client.read.assert_called_once_with()
        assert page.evaluate.call_count == 2
        assert all("anantaMachine.renew" not in call.args[0] for call in page.evaluate.call_args_list)
    finally:
        controls.close()


def test_unknown_speech_state_cannot_authorize_renewal():
    session, page, controls, _media = runtime()
    session.client.assignment["deadline"] = 300
    session.client.read = Mock(return_value={"grant": "synthetic-grant"})
    page.evaluate.side_effect = lambda expression, *_args: (
        {"state": "unknown"} if "speech.status" in expression else {"joined": True, "lease": {"expiresAt": 120_000}})
    try:
        with pytest.raises(ValueError, match="renewal_speech_state_invalid"):
            session.renew()
        assert all("anantaMachine.renew" not in call.args[0] for call in page.evaluate.call_args_list)
    finally:
        controls.close()


def test_task_deadline_does_not_allow_skipping_current_hub_denial():
    session, page, controls, _media = runtime()
    session.client.assignment["deadline"] = 300
    session.client.read = Mock(side_effect=ValueError("synthetic_revocation"))
    try:
        with pytest.raises(ValueError, match="synthetic_revocation"):
            session.renew()
        page.evaluate.assert_not_called()
    finally:
        controls.close()


def test_avatar_pause_closes_camera_and_still_progresses_chat_on_owner_thread():
    session, page, controls, _media = runtime()
    session.avatar_generation = 7
    controls.state["controls"]["avatar"]["enabled"] = False
    try:
        assert session.step() == {"joined": True}
        page.evaluate.assert_called_once_with("(g) => window.anantaMachine.avatar.close(g)", 7)
        assert session.avatar_generation is None
        controls.require_current(controls.checkpoint("chat"))
    finally:
        controls.close()


def test_avatar_revoked_during_media_preparation_is_never_opened():
    session, page, controls, media = runtime()

    def paused(_state, *, cooperate):
        controls.state["controls"]["avatar"] = {"enabled": False, "revision": 2, "since": 2000}
        return {"synthetic": True}

    media.idle.side_effect = paused
    try:
        with pytest.raises(CompanionSourceInterrupted):
            session.avatar()
        page.evaluate.assert_not_called()
    finally:
        controls.close()


def test_speech_pause_closes_audio_without_stopping_chat_or_avatar():
    session, page, controls, _media = runtime()
    controls.state["controls"]["speech"]["enabled"] = False
    try:
        session.step()
        page.evaluate.assert_called_once_with("() => window.anantaMachine.speech.close()")
        controls.require_current(controls.checkpoint("chat"))
        controls.require_current(controls.checkpoint("avatar"))
    finally:
        controls.close()


def test_observation_denial_is_control_loss_and_cannot_become_fallback_answer():
    session, _page, controls, _media = runtime()
    session.observation = Mock()
    session.observation.poll.side_effect = ValueError("synthetic_revocation")
    try:
        with pytest.raises(CompanionControlLost, match="observation_lost"):
            session.step()
        session.pump.step.assert_not_called()
    finally:
        controls.close()


def test_recent_browser_progress_cannot_extend_closed_hub_authority():
    session, _page, controls, _media = runtime()
    session.browser_progress.clock = lambda: 0
    assert session.step() == {"joined": True}
    session.pump.step.assert_called_once()
    controls.close()
    with pytest.raises(CompanionControlLost):
        session.step()
    session.pump.step.assert_called_once()


def test_avatar_retirement_is_immediate_inside_browser_progress_interval():
    session, page, controls, _media = runtime()
    session.browser_progress.clock = lambda: 0
    session.avatar_generation = 7
    session.avatar_revision = controls.state["controls"]["avatar"]
    try:
        session.step()
        page.evaluate.reset_mock()
        controls.state["controls"]["avatar"] = {"enabled": False, "revision": 2, "since": 2000}
        session.step()
        page.evaluate.assert_called_once_with("(g) => window.anantaMachine.avatar.close(g)", 7)
        assert session.avatar_generation is None
        session.pump.step.assert_called_once()
    finally:
        controls.close()


def test_parent_progress_is_only_reported_after_new_current_hub_observation():
    now, controls, state, _pool = fixture()
    client = SimpleNamespace(assignment={"session_id": "synthetic-session"}, deadline=700)
    progress = Mock()
    session = AssignedCompanionSession(Mock(), client, Mock(), Mock(), controls, log=Mock(), progress=progress)
    session.pump = Mock()
    try:
        session.step()
        progress.report.assert_not_called()
        now[0] = 101
        session.step()
        progress.report.assert_not_called()
        now[0] = 102.4
        controls.exchange.pending.set_result(state)
        session.step()
        progress.report.assert_called_once_with(103.5, 700)
        session.step()
        progress.report.assert_called_once()
        now[0] = 103.5
        with pytest.raises(CompanionControlLost):
            session.step()
        progress.report.assert_called_once()
    finally:
        controls.close()


@pytest.mark.parametrize("fails", [False, True])
def test_runtime_entrypoint_closes_assignment_input_and_reports_terminal_once(monkeypatch, fails):
    import worker.meet_media.companion_task_runtime as runtime_module
    from tests.meet.test_meet_companion_task_grant_client import assignment
    from worker.meet_media.contract import encode

    source = io.BytesIO(encode(assignment()))
    monkeypatch.setattr(runtime_module.sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.delenv("ANANTA_MEET_CONTROL_PROGRESS_FD", raising=False)
    monkeypatch.delenv("MEET_HUB_COMPANION_TASK_URL", raising=False)
    hub = SimpleNamespace(url="http://127.0.0.1/api/meet/v1/internal/dialog",
                          key=b"synthetic-private-worker-key-32bytes", call=Mock())
    monkeypatch.setattr("worker.meet_media.dialog_client.HubDialogClient", lambda _assignment: hub)
    run = Mock(side_effect=CompanionControlLost("synthetic_cancel") if fails else None)
    monkeypatch.setattr(runtime_module, "run", run)
    if fails:
        with pytest.raises(CompanionControlLost):
            runtime_module.main()
    else:
        runtime_module.main()
    assert source.closed
    hub.call.assert_called_once_with("finish", status="failed" if fails else "completed")
    client = run.call_args.args[0]
    assert client.endpoint == "http://127.0.0.1/api/meet/v1/internal/companion/task-grant"
    assert run.call_args.kwargs["progress"] is None
