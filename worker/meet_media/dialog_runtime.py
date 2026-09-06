"""One Hub-assigned ephemeral browser. No human capture, profiles or task authority."""

from contextlib import ExitStack
import sys
import time

from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES, parse, validate_assignment
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_chat import DialogChatPump, chat_scope_matches as chat_scope_matches
from worker.meet_media.dialog_screen_pump import DialogScreenPump


def start_audio(page, hub, assignment, state, meet_session):
    receipt = state["authorization"]
    if not state["controls"]["audio"]["enabled"] or assignment["audio_mode"] == "off" or state["audio_job"] is not None:
        return None
    if receipt["lease"]["expiresAt"] <= (time.time() + 35) * 1000:
        return None
    sources = page.evaluate("window.anantaMachine.audio.sources()")
    publication = next((p for p in receipt["publications"] if any(
        s["publicationId"] == p["publicationId"] and s["peerId"] == p["peerId"] for s in sources)), None)
    if publication is None:
        return None
    from worker.meet_media.dialog_audio import DialogAudioPump
    delegated = hub.call("audio", meet_session_id=meet_session, publication_id=publication["publicationId"])
    try:
        return DialogAudioPump(page, hub, assignment, delegated["job"])
    except Exception:
        page.evaluate("window.anantaMachine.audio.close()")
        return None  # The Hub reservation bounds retries; no alternative source is inferred.


def run(assignment, hub):
    from playwright.sync_api import sync_playwright
    if not set(assignment["capabilities"]) <= {"chat.read", "chat.send", "screen.publish", "audio.receive"}:
        raise ValueError("meet_dialog_adapter_unavailable")
    with sync_playwright() as playwright, ExitStack() as cleanup:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
            args=["--autoplay-policy=no-user-gesture-required"])
        cleanup.callback(browser.close)
        context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
        context.add_init_script("""for (const name of ['getUserMedia', 'getDisplayMedia']) {
          navigator.mediaDevices[name] = () => Promise.reject(new Error('human_capture_forbidden'));
        }""")
        page = context.new_page(); page.set_default_timeout(20_000)
        url = assignment["meeting"]["origin"] + "/machine"
        page.goto(url, wait_until="domcontentloaded")
        if page.url != url:
            raise ValueError("meet_machine_navigation_denied")
        page.wait_for_function("Boolean(window.anantaMachine)")
        page.evaluate("([room, grant]) => window.anantaMachine.join(room, grant)",
                      [assignment["meeting"]["room_id"], assignment["meeting"]["grant"]])
        local_status = "(({joined, lease}) => ({joined, lease}))(window.anantaMachine.status())"
        meet_session = page.evaluate(local_status)["lease"]["sessionId"]
        chat = DialogChatPump(page, hub, assignment); cleanup.callback(chat.close)
        screen = DialogScreenPump(page, browser, assignment); cleanup.callback(screen.close)
        audio = None
        # Resolve the current source at teardown, not an obsolete iteration's object.
        cleanup.callback(lambda: audio.close() if audio is not None else None)
        next_exchange = 0; control_revision = 0
        while time.monotonic() < hub.deadline:
            if page.url != url:
                raise ValueError("meet_machine_navigation_denied")
            if time.monotonic() >= next_exchange:
                state = hub.call("exchange", meet_session_id=meet_session)
                receipt, controls = state["authorization"], state["controls"]
                local = page.evaluate(local_status)
                if (not local["joined"] or local["lease"] != receipt["lease"] or receipt["roomId"] != assignment["meeting"]["room_id"]
                        or controls["revision"] < control_revision):
                    raise ValueError("meet_dialog_session_changed")
                control_revision = controls["revision"]
                if state["renewal"]:
                    if audio is not None:
                        audio.close(); audio = None
                    screen.invalidate(); chat.invalidate()
                    page.evaluate("grant => window.anantaMachine.renew(grant)", state["renewal"])
                    next_exchange = 0
                    continue
                if audio is not None:
                    audio.refresh(receipt, state["audio_job"])
                else:
                    audio = start_audio(page, hub, assignment, state, meet_session)
                chat.update(receipt, controls["chat"])
                screen.update(controls["screen"], {"chat": chat.opened is not None, "reply": chat.pending is not None,
                    "audio": audio.stage if audio is not None and not audio.closed else "off"})
                next_exchange = time.monotonic() + 2
            chat.tick(); screen.tick()
            if audio is not None:
                try:
                    audio.tick()
                    if audio.closed:
                        audio = None
                except Exception:
                    audio.close(); audio = None
            page.wait_for_timeout(100)
        page.evaluate("window.anantaMachine.leave()")


def main():
    assignment = validate_assignment(parse(sys.stdin.buffer.read(MAX_DIALOG_BYTES + 1)), time.time())
    hub = HubDialogClient(assignment)
    status = "failed"
    try:
        run(assignment, hub); status = "completed"
    finally:
        try:
            hub.call("finish", status=status)
        except Exception:
            pass  # No contents, grants or upstream exception text in logs.


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
