"""Companion self-heal trigger: Hub and browser are test doubles, no network."""

import base64
import importlib
import io
import json
import sys
import types
import urllib.error
from types import SimpleNamespace

import pytest

from worker.meet_media.contract import signature

pytestmark = pytest.mark.timeout(30)

KEY = b"synthetic-companion-worker-key-000000"
ORIGIN = "https://webrtc.ananta.de"
ROOM_A = "room-" + "a" * 18
ROOM_B = "room-" + "b" * 18


def invite(room):
    return f"{ORIGIN}/?room={room}&mode=room"


@pytest.fixture
def companion(tmp_path, monkeypatch):
    key_file = tmp_path / "worker.key"
    key_file.write_bytes(KEY)
    key_file.chmod(0o600)
    monkeypatch.setenv("MEET_COMPANION_LOG", str(tmp_path / "companion.log"))
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key_file))
    monkeypatch.setenv("ANANTA_MEET_AVATAR_ENABLED", "0")
    monkeypatch.delenv("MEET_HUB_PUBLIC_ROOM_URL", raising=False)
    sys.modules.pop("worker.meet_media.companion", None)
    module = importlib.import_module("worker.meet_media.companion")
    monkeypatch.setattr(module, "ROOM_JSON", str(tmp_path / "room.json"))
    monkeypatch.setattr(module, "STOP_FILE", tmp_path / "companion-stop")
    yield module
    module.LOG.close()
    sys.modules.pop("worker.meet_media.companion", None)


def log_text(companion):
    companion.LOG.flush()
    return open(companion.LOG.name, encoding="utf-8").read()


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def hub(monkeypatch, companion, outcomes):
    calls = []

    def urlopen(request, timeout=None):
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(json.dumps(outcome).encode())

    monkeypatch.setattr(companion.urllib.request, "urlopen", urlopen)
    return calls


def public(room, **fields):
    return {"roomId": room, "inviteUrl": invite(room), "visibility": "public", "reused": False, "revision": 2} | fields


def http_error(status, code):
    body = io.BytesIO(json.dumps({"error": {"code": code}}).encode())
    return urllib.error.HTTPError("http://hub", status, "error", {}, body)


# --- ensure_public_room ----------------------------------------------------


def test_url_is_the_internal_public_room_next_to_the_companion_grant(companion):
    assert companion.PUBLIC_ROOM_URL == "http://meet-authorizing-hub:5000/api/meet/v1/internal/public-room"


def test_ensure_posts_a_signed_project_request_and_returns_the_room(companion, monkeypatch):
    calls = hub(monkeypatch, companion, [public(ROOM_B)])
    assert companion.ensure_public_room() == {"room_id": ROOM_B, "invite_url": invite(ROOM_B)}
    request = calls[0]
    assert request.full_url == companion.PUBLIC_ROOM_URL and request.get_method() == "POST"
    assert json.loads(request.data) == {"project_id": companion.PROJECT}
    assert request.headers["X-ananta-task-signature"] == signature(KEY, request.data)
    assert "public_room room=%s reused=False revision=2" % ROOM_B in log_text(companion)


@pytest.mark.parametrize(
    "answer",
    [
        {"roomId": "room-nope", "inviteUrl": invite("room-nope")},
        public(ROOM_B, inviteUrl="https://evil.test/?room=" + ROOM_B + "&mode=room"),
        public(ROOM_B, inviteUrl=f"{ORIGIN}/?room={ROOM_B}ff&mode=room"),
        ["not", "a", "dict"],
    ],
)
def test_ensure_rejects_a_foreign_or_malformed_answer(companion, monkeypatch, answer):
    hub(monkeypatch, companion, [answer])
    assert companion.ensure_public_room() is None
    assert "public_room_err invalid_response" in log_text(companion)


def test_hub_failure_is_logged_and_never_fatal(companion, monkeypatch):
    hub(monkeypatch, companion, [http_error(502, "meet_public_room_token_unavailable"), OSError("reset")])
    assert companion.ensure_public_room() is None
    assert companion.ensure_public_room() is None
    text = log_text(companion)
    assert "public_room_err status=502 code=meet_public_room_token_unavailable" in text
    assert "public_room_err OSError('reset')" in text


def test_disabled_public_room_stops_further_requests(companion, monkeypatch):
    calls = hub(monkeypatch, companion, [http_error(404, "meet_public_room_disabled")])
    assert companion.ensure_public_room() is None
    assert companion.ensure_public_room() is None
    assert len(calls) == 1


def test_advertise_room_prefers_the_public_invite(companion):
    companion.advertise_room(ROOM_B, invite(ROOM_B) + "&extra=1")
    stored = json.loads(open(companion.ROOM_JSON, encoding="utf-8").read())
    assert stored == {"room_id": ROOM_B, "invite_url": invite(ROOM_B) + "&extra=1", "project": companion.PROJECT}
    assert "ROOM %s %s&extra=1" % (ROOM_B, invite(ROOM_B)) in log_text(companion)


def test_public_invite_only_for_the_joined_room(companion):
    assert companion.public_invite(None, ROOM_A) is None
    assert companion.public_invite({"room_id": ROOM_A, "invite_url": "x"}, ROOM_A) == "x"
    assert companion.public_invite({"room_id": ROOM_B, "invite_url": "x"}, ROOM_A) is None
    assert "public_room_mismatch bound=%s public=%s" % (ROOM_A, ROOM_B) in log_text(companion)


# --- run(): trigger at join and on a lost entry while joined ----------------


def grant(room):
    claims = {"taskId": "task-1", "runtimeId": "runtime-1", "sessionId": "session-" + room[-4:]}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {"room_id": room, "grant": f"h.{payload}.s", "expires_at": 1}


class FakePage:
    def __init__(self, world):
        self.world = world

    def set_default_timeout(self, _value):
        pass

    def on(self, *_args):
        pass

    def goto(self, *_args, **_kwargs):
        pass

    def wait_for_function(self, *_args, **_kwargs):
        pass

    def evaluate(self, script, arg=None):
        world = self.world
        if "anantaMachine.join" in script:
            world.joins.append(arg[0])
            if len(world.joins) >= world.stop_after_joins:
                world.stop_file.touch()
        elif "anantaMachine.leave" in script:
            world.leaves += 1
        elif "anantaMachine.status" in script:
            return {"joined": True, "peers": 1, "e2ee": "active", "chat": []}
        elif "chat.poll" in script:
            return {"events": []}
        return None


def fake_playwright(world):
    browser = SimpleNamespace(
        new_context=lambda **_kwargs: SimpleNamespace(new_page=lambda: FakePage(world)),
        close=lambda: None,
    )

    class Manager:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: browser))

        def __exit__(self, *_exc):
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = Manager
    return {"playwright": types.ModuleType("playwright"), "playwright.sync_api": sync_api}


@pytest.fixture
def world(companion, monkeypatch, tmp_path):
    state = SimpleNamespace(joins=[], leaves=0, stop_after_joins=1, stop_file=tmp_path / "companion-stop")
    for name, module in fake_playwright(state).items():
        monkeypatch.setitem(sys.modules, name, module)
    ticks = iter(range(1_000_000, 2_000_000))
    monkeypatch.setattr(
        companion, "time", SimpleNamespace(time=lambda: float(next(ticks)), sleep=lambda _s: None, monotonic=lambda: 0)
    )
    monkeypatch.setattr(companion, "log_lipsync_service", lambda: None)
    return state


def test_join_self_heals_first_and_advertises_the_rebound_room(companion, monkeypatch, world):
    order = []
    monkeypatch.setattr(companion, "ensure_public_room", lambda: order.append("ensure") or {
        "room_id": ROOM_B, "invite_url": invite(ROOM_B)})
    monkeypatch.setattr(companion, "fetch_grant", lambda identity=None: order.append("grant") or grant(ROOM_B))
    companion.main()
    assert order[:2] == ["ensure", "grant"]
    assert world.joins == [ROOM_B]
    stored = json.loads(open(companion.ROOM_JSON, encoding="utf-8").read())
    assert (stored["room_id"], stored["invite_url"]) == (ROOM_B, invite(ROOM_B))
    assert "ROOM %s %s" % (ROOM_B, invite(ROOM_B)) in log_text(companion)


def test_join_still_happens_when_the_hub_cannot_heal(companion, monkeypatch, world):
    monkeypatch.setattr(companion, "ensure_public_room", lambda: None)
    monkeypatch.setattr(companion, "fetch_grant", lambda identity=None: grant(ROOM_A))
    companion.main()
    assert world.joins == [ROOM_A]
    assert json.loads(open(companion.ROOM_JSON, encoding="utf-8").read())["invite_url"] == invite(ROOM_A)


def test_lost_entry_while_joined_moves_the_companion_to_the_new_room(companion, monkeypatch, world):
    world.stop_after_joins = 2
    monkeypatch.setattr(companion, "PUBLIC_ROOM_CHECK", 3.0)
    binding = {"room": ROOM_A}
    answers = iter([ROOM_A, ROOM_B, ROOM_B])

    def ensure():
        # The Hub re-created the entry on the second check and rebound the project.
        binding["room"] = next(answers)
        return {"room_id": binding["room"], "invite_url": invite(binding["room"])}

    grants = []
    monkeypatch.setattr(companion, "ensure_public_room", ensure)
    monkeypatch.setattr(
        companion, "fetch_grant", lambda identity=None: grants.append(identity) or grant(binding["room"])
    )
    companion.main()
    assert world.joins == [ROOM_A, ROOM_B]
    assert world.leaves == 2
    # The new room starts a fresh machine session, not a renewal of the old one.
    assert grants[-1] is None
    text = log_text(companion)
    assert "ROOM_MOVED %s -> %s" % (ROOM_A, ROOM_B) in text and "rejoin after room move" in text
    assert json.loads(open(companion.ROOM_JSON, encoding="utf-8").read())["room_id"] == ROOM_B


def test_periodic_check_can_be_switched_off(companion, monkeypatch, world):
    world.stop_after_joins = 99
    monkeypatch.setattr(companion, "PUBLIC_ROOM_CHECK", 0.0)
    calls = []
    monkeypatch.setattr(companion, "ensure_public_room", lambda: calls.append(1) or None)
    monkeypatch.setattr(companion, "fetch_grant", lambda identity=None: grant(ROOM_A))
    statuses = iter(range(20))

    original = FakePage.evaluate

    def evaluate(page, script, arg=None):
        if "anantaMachine.status" in script and next(statuses) >= 15:
            return {"joined": False}
        return original(page, script, arg)

    monkeypatch.setattr(FakePage, "evaluate", evaluate)
    # One session only: main() would rejoin after "left" (see companion_supervisor).
    companion.run()
    assert calls == [1] and world.joins == [ROOM_A]
    assert "left" in log_text(companion)
