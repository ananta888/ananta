"""Actual run() composition with a 50-s synthetic model and headless ports."""

import base64
import importlib
import json
import os
import select
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.meet_media.companion_chat_pump import CompanionChatPump
from worker.meet_media.companion_media import AvatarPorts

pytestmark = pytest.mark.timeout(15)


@pytest.fixture
def endpoint_bridge():
    processes = []

    def start():
        node = os.environ.get("ANANTA_TEST_NODE_BINARY")
        bundle = os.environ.get("ANANTA_TEST_MEET_CHAT_ENDPOINT_BUNDLE")
        if not node or not bundle:
            pytest.skip("real client endpoint requires explicit Node and bundled endpoint paths")
        process = subprocess.Popen([node, str(Path(__file__).with_name("meet_chat_endpoint_bridge.mjs"))],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, close_fds=True)
        processes.append(process)

        def call(operation, now, message_count, argument=None):
            process.stdin.write(json.dumps({"operation": operation, "now": now, "message_count": message_count,
                                            "argument": argument}) + "\n")
            process.stdin.flush()
            if not select.select([process.stdout], [], [], 5)[0]:
                pytest.fail("synthetic real endpoint response timeout")
            response = json.loads(process.stdout.readline())
            if "error" in response:
                raise RuntimeError("Page.evaluate: Error: " + response["error"])
            return response["result"]

        return call

    yield start
    for process in processes:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()


def real_endpoint_evaluate(bridge, expression, argument, now, message_count, renewals, replies):
    if ".status()" in expression:
        return bridge("status", now, message_count)
    if ".renew(" in expression:
        renewals.append(now)
        return bridge("renew", now, message_count)
    for operation in ("open", "poll", "redeliver", "reply", "ack"):
        if ".chat." + operation + "(" in expression:
            result = bridge(operation, now, message_count, argument)
            if operation == "reply":
                replies.append(tuple(argument))
            return result
    return None


class CooperativeMedia:
    """Long synthetic idle/render operation, no browser calls in the port."""

    def __init__(self, now):
        self.now, self.calls, self.closed = now, [], False

    def _prepare(self, kind, cooperate):
        self.calls.append(kind)
        for _ in range(50):
            self.now[0] += 1
            cooperate()
        return {"frames": 2}

    def idle(self, _state, *, cooperate):
        return self._prepare("idle", cooperate)

    def speaking(self, _pcm, *, cooperate):
        return [self._prepare("speaking", cooperate)]

    def close(self):
        self.closed = True


@pytest.mark.parametrize("message_count", [2, 3, 32])
@pytest.mark.parametrize("client_transport", ["fixture", "real_endpoint"])
@pytest.mark.parametrize("avatar_on", [False, True])
def test_actual_companion_collects_input_during_long_answer_and_renews(
        monkeypatch, tmp_path, message_count, client_transport, endpoint_bridge, avatar_on):
    monkeypatch.setenv("MEET_COMPANION_LOG", str(tmp_path / "synthetic-companion.log"))
    companion = importlib.import_module("worker.meet_media.companion")
    now, stopped, renewals, answered, replies = [0.0], [False], [], [], []
    claims = {"taskId": "task", "runtimeId": "runtime", "sessionId": "session"}
    token = "synthetic." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".synthetic"
    room = "room-aaaaaaaaaaaaaaaaaa"
    bridge = endpoint_bridge() if client_transport == "real_endpoint" else None

    class Page:
        generation = 1
        opened_generation = None
        queued = []
        admitted = set()
        delivered = {}
        serial = 0

        def set_default_timeout(self, _timeout):
            pass

        def on(self, *args):
            pass

        def goto(self, *args, **kwargs):
            pass

        def wait_for_function(self, *args, **kwargs):
            pass

        def evaluate(self, expression, argument=None):
            if bridge:
                return real_endpoint_evaluate(bridge, expression, argument, now[0], message_count, renewals, replies)
            if ".status()" in expression:
                return {"joined": True, "peers": 1, "e2ee": True, "chat": []}
            if ".renew(" in expression:
                self.generation += 1
                renewals.append(now[0])
            if ".chat.open()" in expression:
                self.opened_generation = self.generation
                self.queued.clear()
                self.serial = 0
                return {"origin": "https://meet.invalid", "tenant_id": "tenant", "project_id": "project",
                        "task_id": "task", "runtime_id": "runtime", "session_id": "session", "lease_id": "lease",
                        "room_id": room, "own_peer_id": "machine", "generation": self.generation,
                        "membership_epoch": 1, "policy_revision": 1, "deadline_ms": 1_000_000}
            if ".chat.poll(" in expression:
                if self.opened_generation != self.generation:
                    raise RuntimeError("Page.evaluate: Error: meet_chat_authority_changed")
                for number, sent_at in [(1, 0), *[(value, 10) for value in range(2, message_count + 1)]]:
                    if now[0] >= sent_at and number not in self.admitted and now[0] - sent_at <= 30:
                        self.admitted.add(number)
                        self.serial += 1
                        self.queued.append({"cursor": self.serial, "sent_at": sent_at, "event": {
                            "message_id": f"{number:032x}", "text": f"synthetic question {number}",
                            "session_id": "session", "room_id": room, "generation": self.generation,
                            "membership_epoch": 1, "sender_peer_id": "human"}})
                self.queued = [item for item in self.queued if now[0] - item["sent_at"] <= 30]
                events = [
                    {"cursor": item["cursor"], "event": item["event"]}
                    for item in self.queued if item["cursor"] > argument][:8]
                for item in events:
                    self.delivered[item["event"]["message_id"]] = now[0]
                return {"schema": "ananta.meet-chat-batch.draft1", "events": events}
            if ".chat.redeliver(" in expression:
                if self.opened_generation != self.generation:
                    raise RuntimeError("Page.evaluate: Error: meet_chat_authority_changed")
                if any(identity not in self.delivered or now[0] - self.delivered[identity] > 120
                       for identity in argument):
                    raise RuntimeError("Page.evaluate: Error: meet_chat_redelivery_denied")
                for identity in argument:
                    self.delivered[identity] = now[0]
                return {"schema": "ananta.meet-chat-redelivery.draft1", "message_ids": argument}
            if ".chat.reply(" in expression:
                if argument[0] not in self.delivered or now[0] - self.delivered[argument[0]] > 120:
                    raise RuntimeError("Page.evaluate: Error: meet_chat_reply_denied")
                replies.append(tuple(argument))

    page = Page()
    browser = SimpleNamespace(new_context=lambda **kwargs: SimpleNamespace(new_page=lambda: page),
                              close=lambda: None)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))
    monkeypatch.setitem(sys.modules, "playwright.sync_api",
                        SimpleNamespace(sync_playwright=lambda: nullcontext(playwright)))

    class Execution:
        closed = False

        def call(self, kind, text, *, cooperate):
            if kind == "answer":
                answered.append(text)
                for _ in range(50):
                    now[0] += 1
                    cooperate()
                return "synthetic answer " + str(len(answered))
            cooperate()
            return b"\x00\x00"

        def close(self):
            self.closed = True

    execution = Execution()
    media = CooperativeMedia(now)
    monkeypatch.setattr(companion, "CompanionExecutionProcess", lambda: execution)
    monkeypatch.setattr(companion, "CompanionMediaPreparationProcess", lambda: media)
    monkeypatch.setattr(companion, "avatar_ports", lambda *_args: AvatarPorts(
        lambda _payload: 1, lambda _generation: None, lambda _samples: 1, lambda *_args: None))
    monkeypatch.setattr(companion, "CompanionChatPump", lambda *args, **kwargs:
                        CompanionChatPump(*args, **kwargs, clock=lambda: now[0]))
    monkeypatch.setattr(companion, "STOP_FILE", SimpleNamespace(exists=lambda: stopped[0]))
    monkeypatch.setattr(companion, "fetch_grant", lambda *args: {"room_id": room, "grant": token})
    monkeypatch.setattr(companion, "ensure_public_room", lambda: None)
    monkeypatch.setattr(companion, "advertise_room", lambda *args: None)
    monkeypatch.setattr(companion, "log_lipsync_service", lambda: None)
    monkeypatch.setattr(companion, "avatar_enabled", lambda: avatar_on)
    diagnostics = []

    def log(message):
        diagnostics.append(message)
        if "chat_control_err" in message:
            stopped[0] = True

    monkeypatch.setattr(companion, "log", log)
    monkeypatch.setattr(companion.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    def speak(*args, **kwargs):
        kwargs["cooperate"]()
        if avatar_on:
            kwargs["clip_preparer"](args[2])
        if len(answered) == message_count:
            stopped[0] = True

    monkeypatch.setattr(companion, "speak", speak)
    companion.run()
    assert not any("chat_control_err" in message for message in diagnostics), diagnostics
    assert answered == [f"synthetic question {number}" for number in range(1, message_count + 1)]
    assert replies == [(f"{number:032x}", f"synthetic answer {number}") for number in range(1, message_count + 1)]
    assert len(renewals) >= 2 and execution.closed
    assert media.closed
    assert media.calls.count("speaking") == (message_count if avatar_on else 0)
