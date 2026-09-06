"""Synthetic lifecycle races; these are not decoded-media acceptance evidence."""
from concurrent.futures import Future
from unittest.mock import Mock

import pytest

from worker.meet_media.dialog_chat import DialogChatPump
from worker.meet_media.dialog_screen_pump import DialogScreenPump
from worker.meet_media.dialog_executor import DialogExecutor
from tests.test_meet_dialog_transport import assignment


def test_late_chat_reply_cannot_cross_a_source_pause_revision():
    page = Mock(); pump = DialogChatPump(page, Mock(), assignment())
    old_scope = {"generation": 1}
    future = Future(); pump.opened = old_scope; pump.revision = 1
    pump.pending = (future, old_scope, "a" * 32, 1)
    pump.invalidate(); pump.revision = 2
    future.set_result({"reply": {"message_id": "a" * 32, "text": "private old answer"}})
    pump.tick()
    assert pump.pending is None
    assert all("chat.reply" not in call.args[0] for call in page.evaluate.call_args_list)
    pump.close()


def test_screen_failure_needs_new_source_consent_and_does_not_reopen_on_tick():
    page = Mock(); page.evaluate.return_value = False
    source = Mock(source_id="screen:session"); factory = Mock(return_value=source)
    pump = DialogScreenPump(page, Mock(), assignment() | {"capabilities": ["screen.publish"]}, factory)
    def evaluate(expression, *args):
        if "status" in expression: return bool(pump.lease)
        if "screen.open" in expression: return {"generation": 1}
        if "screen.push" in expression: raise ValueError("closed")
    page.evaluate.side_effect = evaluate
    control = {"enabled": True, "revision": 1}
    pump.update(control); source.take.return_value = "jpeg"; pump.tick()
    assert pump.failed and pump.source is None
    pump.update(control); pump.tick(); factory.assert_called_once()
    pump.update(control | {"revision": 2}); assert factory.call_count == 2
    pump.update({"enabled": False, "revision": 3}); assert pump.source is None


def test_failed_watchdog_start_kills_child_releases_slot_and_keeps_replay_fence(tmp_path, monkeypatch):
    executor = DialogExecutor(tmp_path / "leases.db", slots=1)
    process = Mock(pid=123456); spawn = Mock(return_value=process)
    kill = Mock(); thread = Mock(); thread.start.side_effect = RuntimeError("thread failed")
    monkeypatch.setattr("worker.meet_media.dialog_executor.subprocess.Popen", spawn)
    monkeypatch.setattr("worker.meet_media.dialog_executor.os.killpg", kill)
    monkeypatch.setattr("worker.meet_media.dialog_executor.threading.Thread", Mock(return_value=thread))
    with pytest.raises(RuntimeError, match="thread failed"): executor.start(assignment())
    kill.assert_called_once(); process.wait.assert_called_once_with(timeout=5)
    assert executor.slots.acquire(blocking=False); executor.slots.release()
    with pytest.raises(ValueError, match="replayed"): executor.start(assignment())
    spawn.assert_called_once()


@pytest.mark.parametrize("failure", ["factory", "open", "close"])
def test_screen_setup_failure_is_isolated_and_requires_a_new_control_revision(failure):
    page = Mock(); source = Mock(source_id="screen:session"); factory = Mock(return_value=source)
    pump = DialogScreenPump(page, Mock(), assignment() | {"capabilities": ["screen.publish"]}, factory)
    if failure == "factory": factory.side_effect = ValueError("source failed")
    def evaluate(expression, *args):
        if "status" in expression: return False
        if "screen.open" in expression: raise ValueError("open failed")
        if failure == "close" and "screen.close" in expression: raise ValueError("target gone")
    page.evaluate.side_effect = evaluate
    pump.update({"enabled": True, "revision": 1})
    assert pump.failed and pump.source is None
    calls = factory.call_count
    pump.update({"enabled": True, "revision": 1}); pump.tick(); pump.close()
    assert factory.call_count == calls
    if failure == "open": source.close.assert_called_once()


@pytest.mark.parametrize("failure", ["binding", "pipeline", "receiver", "pool"])
def test_partial_audio_setup_closes_browser_lease_and_native_resources(failure, monkeypatch):
    from worker.meet_media import dialog_audio as module
    from worker.meet_media import asr_pipeline, audio_receive
    job = {"publication_id": "audio", "deadline": __import__("time").time() + 29}
    monkeypatch.setattr(module, "validate_audio_job", lambda value, now: value)
    binding = Mock(return_value=Mock()); monkeypatch.setattr(module, "bind_subscription", binding)
    pipeline = Mock(); receiver = Mock(); pool = Mock()
    monkeypatch.setattr(asr_pipeline, "MeetAsrPipeline", pipeline)
    monkeypatch.setattr(audio_receive, "MeetAudioReceiver", receiver)
    monkeypatch.setattr(module, "ThreadPoolExecutor", pool)
    {"binding": binding, "pipeline": pipeline, "receiver": receiver, "pool": pool}[failure].side_effect = ValueError("setup failed")
    page = Mock()
    with pytest.raises(ValueError, match="setup failed"):
        module.DialogAudioPump(page, Mock(), assignment(), job)
    assert page.evaluate.call_args.args[0] == "window.anantaMachine.audio.close()"
    if failure in {"receiver", "pool"}: pipeline.return_value.cancel.assert_called_once()
    if failure == "pool": receiver.return_value.close.assert_called_once()
