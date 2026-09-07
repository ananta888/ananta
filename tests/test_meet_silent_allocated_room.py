"""Real single-host private browser gate, synthetic policy, no GPU/release claim."""

import base64
import hashlib
import os
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

pytestmark = [
    pytest.mark.timeout(240),
    pytest.mark.skipif(
        os.environ.get("MEET_SILENT_ROOM_GATE") != "1", reason="explicit private silent-room browser gate required"
    ),
]


def test_real_hub_allocates_silent_first_machine_then_human_joins_and_hub_stops(
    app, tmp_path, monkeypatch, record_property
):
    from playwright.sync_api import BrowserType

    from agent.services.private_container_network_policy import pin_private_container_address
    from tests.meet_companion_build import require_current_browser_build
    from tests.meet_dialog_browser_fixture import DialogBrowserFixture
    from tests.meet_silent_room_driver import SilentRoomDriver
    from tests.meet_silent_room_hub import SilentRoomHub

    repository = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    record_property(
        "browser_build_preflight", require_current_browser_build(repository, os.environ.get("MEET_TEST_PUBLIC_DIR"))
    )
    key = Ed25519PrivateKey.generate()
    private, public, hmac_file = (tmp_path / name for name in ("hub.pem", "hub-public.pem", "worker-key"))
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    worker_key = b"synthetic-private-silent-room-key-32"
    hmac_file.write_bytes(worker_key)
    hmac_file.chmod(0o600)
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(hmac_file))
    driver = SilentRoomDriver(repository, public)
    browser = hub = None
    try:
        ready = driver.receive(timeout=60)
        assert set(ready) == {"origin", "certificate", "test_network"}
        monkeypatch.setenv("SSL_CERT_FILE", ready["certificate"])
        certificate = x509.load_pem_x509_certificate(Path(ready["certificate"]).read_bytes())
        spki = base64.b64encode(
            hashlib.sha256(
                certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            ).digest()
        ).decode()
        browser = DialogBrowserFixture(ready["test_network"], 240)
        browser.start(spki)
        monkeypatch.setattr(BrowserType, "launch", lambda kind, *args, **kwargs: browser.launch(kind, *args, **kwargs))
        monkeypatch.setattr(
            "agent.services.meet_media_transport.pin_private_container_address",
            lambda host, port: "127.0.0.1" if host == "127.0.0.1" else pin_private_container_address(host, port),
        )
        hub = SilentRoomHub(app, ready["origin"], private, worker_key, monkeypatch)
        allocated = hub.allocate()
        assert allocated["room_verified"] is False and allocated["membership_granted"] is False
        room = hub.binding.profile.parse_invite(allocated["invite_url"])
        assert driver.command("bind " + room) == {"bound_empty_room": True}
        assert driver.command("inspect")["participants"] == 0
        hub.start()
        deadline = time.monotonic() + 25
        while True:
            first = driver.command("inspect", timeout=min(2, max(0.01, deadline - time.monotonic())))
            assert time.monotonic() < deadline and not hub.execution.finished.is_set(), hub.execution.failures
            if first["machines"] == 1 and hub.exchanges >= 2:
                break
            time.sleep(0.1)
        assert first == {
            "participants": 1,
            "machines": 1,
            "machine_publications": 0,
            "fixed_ki_label": True,
            "human_captures": 0,
        }
        assert driver.command("join-human") == first | {"participants": 2}
        # Check fresh actual Hub authority after the membership change as well.
        prior_exchanges, deadline = hub.exchanges, time.monotonic() + 5
        while hub.exchanges < prior_exchanges + 2:
            assert time.monotonic() < deadline and not hub.execution.finished.is_set(), hub.execution.failures
            time.sleep(0.05)
        assert driver.command("inspect") == first | {"participants": 2}
        assert not hub.execution.finished.is_set(), hub.execution.failures
        stop_at = time.monotonic()
        hub.stop()
        while True:
            final = driver.command("inspect", timeout=2)
            observed_stop_ms = (time.monotonic() - stop_at) * 1000
            assert observed_stop_ms < 5000, "silent machine did not leave within stop budget"
            if final["machines"] == 0:
                break
            time.sleep(0.05)
        assert final == first | {"participants": 1, "machines": 0}
        assert hub.execution.finished.wait(5)
        assert hub.execution.failures in ([], ["meet_dialog_hub_revoked_or_unavailable"])
        hub.media.execute.assert_not_called()
        with app.app_context():
            assert hub.tasks.get_by_id(hub.started["task_id"]).status == "cancelled"
        record_property(
            "silent_allocated_room",
            {
                "synthetic": True,
                "production_release_evidence": False,
                "single_host": True,
                "first_machine": first,
                "visible_ki_label_after_join": True,
                "validated_hub_exchanges": hub.exchanges,
                "stop_ms": round(observed_stop_ms, 2),
            },
        )
    finally:
        try:
            if hub is not None:
                hub.close()
        finally:
            try:
                if browser is not None:
                    browser.close()
            finally:
                driver.close()
