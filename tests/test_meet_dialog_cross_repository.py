"""Real Hub/Worker/Meet wires and browsers; synthetic policy and model result.

This proves neither GPU inference nor production trust/NAT. Opt in with
MEET_CROSS_REPOSITORY_GATE=1 and build the adjacent Meet repository first.
"""

import base64
import hashlib
import json
import os
import re
import selectors
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

SOAK_SECONDS = int(os.environ.get("MEET_DIALOG_SOAK_SECONDS", "0"))
if SOAK_SECONDS != 0 and not 300 <= SOAK_SECONDS <= 7200:
    raise ValueError("test_soak_seconds_must_be_zero_or_300_to_7200")

pytestmark = [
    pytest.mark.timeout(240 + SOAK_SECONDS),
    pytest.mark.skipif(
        os.environ.get("MEET_CROSS_REPOSITORY_GATE") != "1",
        reason="opt-in cross-repository browser gate; not GPU/TURN evidence",
    ),
]


def process_usage(*roots):
    import psutil

    processes = {p.pid: p for root in roots for p in [root, *root.children(recursive=True)]}
    rss = 0
    for process in processes.values():
        try:
            rss += process.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return rss, len(processes)


def close_bridge(bridge):
    """Reap only the process group this test created, including failed setup."""
    running = bridge.poll() is None
    try:
        try:
            if running:
                bridge.stdin.write("stop\n")
                bridge.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            # EOF releases the Node readline input as well as its resources;
            # leaving stdin open until wait() completes can keep Node alive.
            try:
                bridge.stdin.close()
            except BrokenPipeError:
                pass
        if running:
            try:
                # The private fixture now owns three bounded Docker cleanup
                # operations; the old ten-second allowance could interrupt them.
                bridge.wait(timeout=100)
            except subprocess.TimeoutExpired:
                import signal

                os.killpg(bridge.pid, signal.SIGKILL)
                bridge.wait(timeout=5)
    finally:
        bridge.stdout.close()


@pytest.mark.parametrize("spoken_mode", [False, True], ids=["text", "speech"])
def test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop(app, tmp_path, monkeypatch, spoken_mode):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    from playwright.sync_api import Browser, BrowserType
    from sqlmodel import Session
    from werkzeug.serving import WSGIRequestHandler, make_server

    from agent.database import engine
    from agent.db_models.projects import ProjectDB
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.services.meet_authorization_client import MeetAuthorizationClient
    from agent.services.meet_chat_policy import ChatReplyPolicy
    from agent.services.meet_contract import MeetError, MeetProfile
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_replies import MeetDialogReplies
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.meet_turn_service import HubMediaTasks
    from agent.services.source_control_access_policy import HubSourcePrincipal
    from tests.meet_dialog_browser_fixture import DialogBrowserFixture
    from tests.meet_dialog_speech_observer import DialogSpeechObserver
    from worker.meet_media.dialog_chat import DialogChatPump
    from worker.meet_media.dialog_client import HubDialogClient
    from worker.meet_media.dialog_runtime import run
    from worker.meet_media.dialog_screen import OwnedDialogScreen
    from worker.meet_media.dialog_screen_pump import DialogScreenPump
    from worker.meet_media.server import create_server

    meet = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    assert (meet / "dist/browser/index.html").is_file(), "Build the adjacent Meet repository first"
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "hub.pem"
    public = tmp_path / "hub-public.pem"
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    hmac_key = b"synthetic-private-dialog-test-key-32"
    key_path = tmp_path / "worker-key"
    key_path.write_bytes(hmac_key)
    key_path.chmod(0o600)
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key_path))
    # Task scopes are real foreign keys, even with synthetic access policy.
    # Seed the owning project before any threaded Hub task ingestion; otherwise
    # a newly opened SQLite connection can expose the missing fixture parent.
    with Session(engine) as session:
        session.add(
            ProjectDB(
                tenant_id="synthetic",
                project_id="synthetic",
                name="Synthetic dialog gate",
                created_by_subject_id="owner",
            )
        )
        session.commit()
    bridge = subprocess.Popen(
        ["node", "test/helpers/machine-hub-bridge.mjs"],
        cwd=meet,
        env=os.environ | {"MEET_TEST_HUB_PUBLIC_KEY": str(public)},
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    def receive(timeout=20):
        with selectors.DefaultSelector() as selector:
            selector.register(bridge.stdout, selectors.EVENT_READ)
            assert selector.select(timeout), "bounded Meet bridge response missing"
            line = bridge.stdout.readline(8193)
        assert line and len(line) <= 8192, "Meet bridge exited or exceeded response budget"
        return json.loads(line)

    def command(name):
        bridge.stdin.write(name + "\n")
        bridge.stdin.flush()
        return receive()

    hub = worker = None
    runtime_thread = None
    service = None
    started = None
    browser_fixture = None
    principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
    try:
        # Provisioning private Docker/TLS/STUN resources has its own deadline;
        # normal bridge operations retain the twenty-second response budget.
        ready = receive(timeout=60)
        assert set(ready) == {"origin", "room_id", "certificate", "test_network"}, ready
        monkeypatch.setenv("SSL_CERT_FILE", ready["certificate"])
        cert = x509.load_pem_x509_certificate(Path(ready["certificate"]).read_bytes())
        spki = base64.b64encode(
            hashlib.sha256(cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).digest()
        ).decode()
        browser_fixture = DialogBrowserFixture(ready["test_network"], SOAK_SECONDS + 180)
        browser_fixture.start(spki)

        def trusted_fixture_launch(browser_type, *args, **kwargs):
            return browser_fixture.launch(browser_type, *args, **kwargs)

        monkeypatch.setattr(BrowserType, "launch", trusted_fixture_launch)
        new_context = Browser.new_context

        def observed_context(browser, *args, **kwargs):
            context = new_context(browser, *args, **kwargs)
            context.add_init_script("""window.__testPcs = [];
              window.__testIce = {emitted:0, mdns:0, received:0, failed:0};
              const Native = window.RTCPeerConnection;
              window.RTCPeerConnection = class extends Native {
                constructor(...args) { super(...args); window.__testPcs.push(this);
                  this.addEventListener('icecandidate', e => { if (e.candidate) {
                    window.__testIce.emitted++; if (e.candidate.candidate.includes('.local')) window.__testIce.mdns++;
                  }});
                }
                async addIceCandidate(candidate) { window.__testIce.received++;
                  try { return await super.addIceCandidate(candidate); }
                  catch (e) { window.__testIce.failed++; throw e; }
                }
              };""")
            return context

        monkeypatch.setattr(Browser, "new_context", observed_context)
        # Only the private-container network boundary is substituted for local
        # loopback in this test. Production pinning has its own negative tests.
        monkeypatch.setattr("agent.services.meet_media_transport.pin_private_container_address", lambda *_: "127.0.0.1")

        class Binding:
            profile = MeetProfile(ready["origin"])

            def require_write_access(self, actor, project, task=""):
                if actor != principal or project != "synthetic":
                    raise MeetError("test_scope_denied", 403)

            def read(self, actor, project, task=""):
                self.require_write_access(actor, project, task)
                return {"invite_url": self.profile.invite(ready["room_id"])}

        binding = Binding()
        tasks = HubDialogTasks()
        speech_observer = DialogSpeechObserver(spoken_mode, monkeypatch)
        capabilities = speech_observer.capabilities
        authority = MeetDialogAuthority(tasks, binding, {("synthetic", "synthetic"): capabilities})
        issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", private)
        reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
        reservations.initialize()
        dispatches.initialize()
        completed = threading.Event()
        chat_ready = threading.Event()
        failures = []
        inject_private_frame = threading.Event()
        take_frame = OwnedDialogScreen.take

        def source_with_test_mutation(source):
            if inject_private_frame.is_set():
                inject_private_frame.clear()
                # Synthetic marker only. Mutate in the source-owning thread;
                # unknown input/content must stop before any frame is published.
                source.page.evaluate("""() => { document.body.replaceChildren(document.createElement('input'));
                  document.body.style.background = '#ff00ff'; document.body.style.height = '100vh';
                  document.querySelector('input').value = 'SYNTHETIC_PRIVATE_MARKER'; }""")
            return take_frame(source)

        monkeypatch.setattr(OwnedDialogScreen, "take", source_with_test_mutation)
        generations = set()
        chat_condition = threading.Condition()
        chat_policy_revision = 0
        screen_debug = {}
        next_screen_debug = 0
        tick_screen = DialogScreenPump.tick

        def observe_screen_tick(pump):
            nonlocal next_screen_debug
            tick_screen(pump)
            if time.monotonic() >= next_screen_debug:
                next_screen_debug = time.monotonic() + 1
                screen_debug.update(
                    failed=pump.failed,
                    source=pump.source is not None,
                    source_lease=pump.lease is not None,
                    sequence=pump.sequence,
                )
                try:
                    screen_debug.update(
                        pump.page.evaluate(
                            "({screen: window.anantaMachine.screen.status(),"
                            " e2ee: window.anantaMachine.status().e2ee, iceCounts:window.__testIce})"
                        )
                    )
                    screen_debug["peers"] = pump.page.evaluate("""window.__testPcs.map(pc => ({
                      connection: pc.connectionState, ice: pc.iceConnectionState, signaling: pc.signalingState,
                      localDescription: Boolean(pc.localDescription), remoteDescription: Boolean(pc.remoteDescription)
                    }))""")
                except Exception:
                    screen_debug["page_unavailable"] = True

        monkeypatch.setattr(DialogScreenPump, "tick", observe_screen_tick)
        update_chat = DialogChatPump.update

        def observe_chat_ready(pump, *args):
            nonlocal chat_policy_revision
            result = update_chat(pump, *args)
            generations.add(args[0]["lease"]["generation"])
            if pump.opened is not None:
                chat_ready.set()
                with chat_condition:
                    chat_policy_revision = pump.opened["policy_revision"]
                    chat_condition.notify_all()
            return result

        monkeypatch.setattr(DialogChatPump, "update", observe_chat_ready)

        def execute_runtime(assignment):
            client = HubDialogClient(assignment)
            try:
                run(assignment, client)
            except Exception as error:
                codes = re.findall(r"\bmeet_[a-z_]{1,64}\b", str(error))
                failures.append(codes[0] if codes else type(error).__name__)
            finally:
                try:
                    client.call("finish", status="failed")
                except ValueError:
                    pass
                completed.set()

        class DialogExecution:
            def start(self, assignment):
                nonlocal runtime_thread
                runtime_thread = threading.Thread(target=execute_runtime, args=(assignment,), daemon=True)
                runtime_thread.start()
                return {
                    "schema": "ananta.meet-dialog-accepted.v1",
                    **{k: assignment[k] for k in ("task_id", "lease_id", "runtime_id")},
                    "status": "accepted",
                }

        # Contract fixture, NOT a claim that the declared CUDA engines executed.
        media = Mock()
        media.execute.side_effect = speech_observer.execute
        worker = create_server(("127.0.0.1", 0), hmac_key, media, DialogExecution())
        threading.Thread(target=worker.serve_forever, daemon=True).start()
        transport = HttpMediaWorker(f"http://127.0.0.1:{worker.server_port}/v1/turns", hmac_key)
        service = MeetDialogService(
            authority,
            tasks,
            MeetAuthorizationClient(authority, issuer),
            issuer,
            transport,
            transport,
            reservations,
            dispatches,
            replies=MeetDialogReplies(
                binding,
                transport,
                HubMediaTasks(),
                dispatches,
                speech_profile=speech_observer.profile,
            ),
        )
        app.config["ROLE"] = "hub"
        app.extensions.update(meet_binding_service=binding, meet_dialog_service=service, meet_media_worker_key=hmac_key)

        class Quiet(WSGIRequestHandler):
            def log(self, *_args, **_kwargs):
                pass

        hub = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        monkeypatch.setenv("MEET_HUB_DIALOG_URL", f"http://127.0.0.1:{hub.server_port}/api/meet/v1/internal/dialog")
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        with app.app_context():
            started = service.start(
                principal,
                "synthetic",
                {
                    "capabilities": capabilities,
                    "duration_seconds": SOAK_SECONDS or 90,
                    "chat_mode": "mention",
                },
            )
        started_at = time.monotonic()
        consent = command("consent")
        assert consent == {"consent": True}, {
            "consent": consent,
            "runtime_errors": failures,
            "runtime_exited": completed.is_set(),
        }
        # Wait for one fresh Hub-authorized queue activation; no old chat replay.
        first_screen = command("screen")
        assert first_screen == {"moving_screen": True}, json.dumps(
            {
                "screen": first_screen,
                "runtime_errors": failures,
                "source": screen_debug,
                "runtime_exited": completed.is_set(),
            }
        )
        assert chat_ready.wait(8), failures
        with app.app_context():
            state = service.control(
                principal,
                "synthetic",
                started["task_id"],
                {"expected_revision": 1, "chat": True, "audio": False, "screen": False},
            )
            assert state["controls"]["screen"]["enabled"] is False
            assert state["controls"]["chat"]["revision"] == 1
        assert command("screen_absent") == {"screen_absent": True}
        ask = command("ask")
        assert ask == {"sent": True}, {"ask": ask, "runtime_errors": failures, "exited": completed.is_set()}
        answer = command("answer")
        assert answer == {"received": True}, {
            "answer": answer,
            "media_calls": media.execute.call_count,
            "runtime_errors": failures,
        }
        media.execute.assert_called_once()
        speech_observer.require_completed(1, completed, failures)
        last_answer_at = time.monotonic()
        with app.app_context():
            service.control(
                principal,
                "synthetic",
                started["task_id"],
                {"expected_revision": 2, "chat": True, "audio": False, "screen": True},
            )
        assert command("screen") == {"moving_screen": True}

        def renewed_consent_round_trip():
            nonlocal last_answer_at
            with chat_condition:
                previous = chat_policy_revision
            assert command("consent") == {"consent": True}
            with chat_condition:
                assert chat_condition.wait_for(lambda: chat_policy_revision > previous, timeout=12), {
                    "fresh_chat_queue_missing": True,
                    "runtime_errors": failures,
                    "previous_revision": previous,
                    "observed_revision": chat_policy_revision,
                }
            assert not completed.is_set(), failures
            # Keep the real Hub cooldown; do not turn a policy rejection into a
            # missing-answer failure just to shorten this regression scenario.
            cooldown = ChatReplyPolicy().cooldown_ms / 1000 + 0.1
            assert not completed.wait(max(0, last_answer_at + cooldown - time.monotonic())), failures
            assert command("ask") == {"sent": True}
            answer = command("answer")
            assert answer == {"received": True}, {
                "answer": answer,
                "media_calls": media.execute.call_count,
                "runtime_errors": failures,
                "policy_revision": chat_policy_revision,
                "runtime_exited": completed.is_set(),
            }
            last_answer_at = time.monotonic()

        # Exercise consent replacement in the short gate too. No question is
        # emitted before a genuinely fresh Hub-matched browser queue exists.
        renewed_consent_round_trip()
        speech_observer.require_completed(2, completed, failures)
        with app.app_context():
            speech_observer.pause_and_require_text(
                lambda: service.control(
                    principal,
                    "synthetic",
                    started["task_id"],
                    {
                        "expected_revision": 3,
                        "chat": True,
                        "audio": False,
                        "screen": True,
                        "speech": False,
                    },
                ),
                renewed_consent_round_trip,
                media,
            )
        if SOAK_SECONDS:
            # Real clocks, real browser/Hub lease renewals; no accelerated timers
            # or synthetic GPU claims. The final five seconds reserve stop budget.
            import psutil

            process = psutil.Process()
            peaks = {"rss_bytes": 0, "processes": 0}
            observations = 0
            next_question = started_at + 240
            while time.monotonic() < started_at + SOAK_SECONDS - 5:
                remaining = started_at + SOAK_SECONDS - 5 - time.monotonic()
                assert not completed.wait(min(45, remaining)), {"runtime_exited": True, "codes": failures}
                if time.monotonic() >= started_at + SOAK_SECONDS - 5:
                    break
                screen_state = command("screen")
                assert screen_state == {"moving_screen": True}, {
                    "screen": screen_state,
                    "runtime_errors": failures,
                    "generations": len(generations),
                    "completed": completed.is_set(),
                    "source": screen_debug,
                }
                browser_process = psutil.Process(browser_fixture.process_id)
                rss, process_count = process_usage(process, browser_process)
                peaks["rss_bytes"] = max(peaks["rss_bytes"], rss)
                peaks["processes"] = max(peaks["processes"], process_count)
                assert rss < 3 * 1024**3 and process_count < 80, peaks
                observations += 1
                if time.monotonic() >= next_question:
                    # Explicit synthetic publisher consent, never an automatic
                    # production extension. Stay within the 40-reply Hub budget.
                    renewed_consent_round_trip()
                    next_question = time.monotonic() + 240
                print(
                    json.dumps(
                        {
                            "synthetic_dialog_soak": "running",
                            "seconds": int(time.monotonic() - started_at),
                            "generations": len(generations),
                            "observations": observations,
                            **peaks,
                        }
                    ),
                    flush=True,
                )
            assert len(generations) >= 4 and observations >= 4
            print(
                json.dumps(
                    {
                        "synthetic_dialog_soak": "measured",
                        "task_seconds": SOAK_SECONDS,
                        "observed_seconds": int(time.monotonic() - started_at),
                        "generations": len(generations),
                        **peaks,
                    }
                ),
                flush=True,
            )
        inject_private_frame.set()
        assert command("private_frame_absent") == {"private_frame_absent": True}
        with app.app_context():
            state = service.inspect(principal, "synthetic", started["task_id"], stop=True)
            assert state["status"] == "cancelled"
        assert completed.wait(10), "Worker did not stop after Hub task cancellation"
        assert command("alone") == {"alone": True}
    finally:
        try:
            if service is not None and started is not None:
                with app.app_context():
                    service.inspect(principal, "synthetic", started["task_id"], stop=True)
            if runtime_thread is not None:
                runtime_thread.join(timeout=10)
            for server in (hub, worker):
                if server is not None:
                    server.shutdown()
                    server.server_close()
        finally:
            try:
                if browser_fixture is not None:
                    browser_fixture.close()
            finally:
                close_bridge(bridge)
