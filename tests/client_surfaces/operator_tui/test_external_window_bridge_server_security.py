from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import client_surfaces.operator_tui.windowing.bridge_server as bridge_module
from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer


def test_bridge_state_requires_token() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.start()
    try:
        url = f"http://127.0.0.1:{bridge.status().port}/state"
        req = urllib.request.Request(url, method="GET")
        try:
            urllib.request.urlopen(req, timeout=2)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 401
            assert body.get("reason_code") == "window_bridge_unauthorized"
    finally:
        bridge.stop()


def test_bridge_auth_context_is_local_authenticated_and_one_time() -> None:
    bridge = ExternalWindowBridgeServer()
    angular_origin = "http://127.0.0.1:4200"
    bridge.set_allowed_browser_origin(angular_origin)
    bridge.start()
    bridge.publish_auth_context(
        {
            "hub_url": "https://hub.example.test",
            "hub_token": "hub-secret",
            "oidc_token": "oidc-secret",
        }
    )
    try:
        base = f"http://127.0.0.1:{bridge.status().port}"
        url = f"{base}/auth-context"
        with urllib.request.urlopen(f"{base}/window", timeout=2) as window_response:
            html = window_response.read().decode("utf-8")
        assert bridge.session_token not in html
        legacy_token_match = re.search(r'const TOKEN = "([a-f0-9]{32})";', html)
        assert legacy_token_match is not None
        legacy = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Origin": angular_origin,
                "X-Ananta-Window-Token": legacy_token_match.group(1),
            },
        )
        try:
            urllib.request.urlopen(legacy, timeout=2)
            assert False, "least-privilege window token must not read auth context"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        unauthorized = urllib.request.Request(url, method="GET", headers={"Origin": angular_origin})
        try:
            urllib.request.urlopen(unauthorized, timeout=2)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        authorized = urllib.request.Request(
            url,
            method="GET",
            headers={"Origin": angular_origin, "X-Ananta-Window-Token": bridge.session_token},
        )
        with urllib.request.urlopen(authorized, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.headers["Cache-Control"] == "no-store"
            assert payload == {
                "ok": True,
                "auth": {
                    "hub_url": "https://hub.example.test",
                    "hub_token": "hub-secret",
                    "oidc_token": "oidc-secret",
                },
            }

        try:
            urllib.request.urlopen(authorized, timeout=2)
            assert False, "expected one-time context to be consumed"
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 410
            assert body["reason_code"] == "window_bridge_auth_context_consumed"
    finally:
        bridge.stop()


def test_bridge_auth_context_expires_fail_closed(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(bridge_module.time, "monotonic", lambda: now)
    bridge = ExternalWindowBridgeServer()
    bridge.start()
    bridge.publish_auth_context({"hub_url": "https://hub.example.test", "hub_token": "secret", "oidc_token": ""})

    now = 161.0

    try:
        assert bridge.consume_auth_context(bridge.session_token) == ("unavailable", None)
    finally:
        bridge.stop()


def test_bridge_cors_is_pinned_to_the_selected_angular_origin() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.set_allowed_browser_origin("http://127.0.0.1:4200")
    bridge.start()
    try:
        url = f"http://127.0.0.1:{bridge.status().port}/state"
        allowed = urllib.request.Request(
            url,
            method="OPTIONS",
            headers={"Origin": "http://127.0.0.1:4200"},
        )
        with urllib.request.urlopen(allowed, timeout=2) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:4200"

        denied = urllib.request.Request(
            url,
            method="OPTIONS",
            headers={"Origin": "http://127.0.0.1:9999"},
        )
        try:
            urllib.request.urlopen(denied, timeout=2)
            assert False, "unexpected loopback origin must be denied"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        bridge.stop()


def test_bridge_rotates_main_token_before_republishing_auth_context() -> None:
    angular_origin = "http://127.0.0.1:4200"
    bridge = ExternalWindowBridgeServer()
    bridge.set_allowed_browser_origin(angular_origin)
    bridge.start()
    bridge.publish_auth_context({"hub_url": "https://hub.example.test", "hub_token": "first", "oidc_token": ""})
    old_token = bridge.session_token
    bridge.stop()

    bridge.start()
    bridge.publish_auth_context({"hub_url": "https://hub.example.test", "hub_token": "second", "oidc_token": ""})
    try:
        assert bridge.session_token != old_token
        stale_action_status, stale_action = bridge.enqueue_action(
            action_id="view.next",
            args={},
            event_id="old-lifecycle",
            token=old_token,
        )
        assert stale_action_status == 401
        assert stale_action["reason_code"] == "window_bridge_unauthorized"
        assert bridge.drain_events() == []
        url = f"http://127.0.0.1:{bridge.status().port}/auth-context"
        stale = urllib.request.Request(
            url,
            method="GET",
            headers={"Origin": angular_origin, "X-Ananta-Window-Token": old_token},
        )
        try:
            urllib.request.urlopen(stale, timeout=2)
            assert False, "a token from the prior lifecycle must be revoked"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        current = urllib.request.Request(
            url,
            method="GET",
            headers={"Origin": angular_origin, "X-Ananta-Window-Token": bridge.session_token},
        )
        with urllib.request.urlopen(current, timeout=2) as response:
            assert json.loads(response.read().decode("utf-8"))["auth"]["hub_token"] == "second"
    finally:
        bridge.stop()


def test_bridge_rejects_hub_token_without_bound_hub_origin() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.start()
    try:
        try:
            bridge.publish_auth_context({"hub_url": "", "hub_token": "secret", "oidc_token": ""})
            assert False, "a Hub bearer must be bound to an explicit Hub origin"
        except ValueError as exc:
            assert str(exc) == "window_bridge_hub_authority_required"
    finally:
        bridge.stop()


def test_bridge_rejects_concurrent_duplicate_actions_atomically() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.start()
    barrier = threading.Barrier(2)
    payload = json.dumps({"action_id": "view.next", "args": {}, "event_id": "same-event"}).encode()

    def submit() -> int:
        barrier.wait(timeout=2)
        request = urllib.request.Request(
            f"http://127.0.0.1:{bridge.status().port}/action",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Ananta-Window-Token": bridge.session_token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = sorted(executor.map(lambda _: submit(), range(2)))
        assert statuses == [202, 409]
        events = bridge.drain_events()
        assert len(events) == 1
        assert events[0].event_id == "same-event"
    finally:
        bridge.stop()
