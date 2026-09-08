"""One Hub-assigned ephemeral browser. No human capture, profiles or task authority."""

import sys
import time
from contextlib import ExitStack

from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES, parse, validate_assignment
from ananta_contracts.meet_source_profile import dialog_source_profile
from worker.meet_media.dialog_avatar_presentation import DialogAvatarPresentation
from worker.meet_media.dialog_avatar_pump import DialogAvatarPump
from worker.meet_media.dialog_chat import DialogChatPump
from worker.meet_media.dialog_chat import chat_scope_matches as chat_scope_matches
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_control_exchange import DialogControlExchange
from worker.meet_media.dialog_screen_pump import DialogScreenPump
from worker.meet_media.dialog_session_binding import require_dialog_session
from worker.meet_media.dialog_session_operations import DialogSessionOperations
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


def start_audio(page, hub, assignment, state, meet_session):
    receipt = state["authorization"]
    if not state["controls"]["audio"]["enabled"] or assignment["audio_mode"] == "off" or state["audio_job"] is not None:
        return None
    if receipt["lease"]["expiresAt"] <= (time.time() + 35) * 1000:
        return None
    sources = page.evaluate("window.anantaMachine.audio.sources()")
    publication = next(
        (
            p
            for p in receipt["publications"]
            if any(s["publicationId"] == p["publicationId"] and s["peerId"] == p["peerId"] for s in sources)
        ),
        None,
    )
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
    # The fixed installed handler defines source classes. The signed closed v1
    # envelope carries capabilities/options, not a Worker-selected capture mode.
    dialog_source_profile(assignment["capabilities"], avatar_images=assignment.get("avatar_images", False))
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, ExitStack() as cleanup:
        browser = playwright.chromium.launch(
            headless=True, chromium_sandbox=True, args=["--autoplay-policy=no-user-gesture-required"]
        )
        cleanup.callback(browser.close)
        context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
        context.add_init_script("""for (const name of ['getUserMedia', 'getDisplayMedia']) {
          navigator.mediaDevices[name] = () => Promise.reject(new Error('human_capture_forbidden'));
        }""")
        page = context.new_page()
        page.set_default_timeout(20_000)
        url = assignment["meeting"]["origin"] + "/machine"
        page.goto(url, wait_until="domcontentloaded")
        if page.url != url:
            raise ValueError("meet_machine_navigation_denied")
        session = DialogSessionOperations(page, url=url, deadline=hub.deadline)
        cleanup.callback(session.close)
        session.ready(assignment["capabilities"])
        session.join(assignment["meeting"]["room_id"], assignment["meeting"]["grant"])
        local_status = "(({joined, lease}) => ({joined, lease}))(window.anantaMachine.status())"
        meet_session = page.evaluate(local_status)["lease"]["sessionId"]
        speech = DialogSpeechOutput(page, assignment)
        cleanup.callback(speech.close)
        chat = DialogChatPump(page, hub, assignment, speech=speech)
        cleanup.callback(chat.close)
        screen = DialogScreenPump(page, browser, assignment)
        cleanup.callback(screen.close)
        avatar = (
            DialogAvatarPresentation(page, hub, assignment)
            if assignment.get("avatar_images") is True
            else DialogAvatarPump(page, assignment)
        )
        cleanup.callback(avatar.close)
        audio = None
        # Resolve the current source at teardown, not an obsolete iteration's object.
        cleanup.callback(lambda: audio.close() if audio is not None else None)
        exchange = DialogControlExchange(hub, meet_session)
        cleanup.callback(exchange.close)
        control_revision = 0
        while time.monotonic() < hub.deadline:
            if page.url != url:
                raise ValueError("meet_machine_navigation_denied")
            state = exchange.poll(refresh_marker=chat.pending_refresh if chat.needs_refresh else None)
            if state is not None:
                receipt, controls = state["authorization"], state["controls"]
                local = page.evaluate(local_status)
                require_dialog_session(
                    local,
                    receipt,
                    controls,
                    room_id=assignment["meeting"]["room_id"],
                    previous_revision=control_revision,
                )
                control_revision = controls["revision"]
                if state["renewal"]:
                    if audio is not None:
                        audio.close()
                        audio = None
                    screen.invalidate()
                    avatar.invalidate()
                    chat.invalidate()
                    session.renew(state["renewal"], exchange.require_fresh)
                    exchange.refresh()
                    continue
                if audio is not None:
                    audio.refresh(receipt, state["audio_job"])
                else:
                    audio = start_audio(page, hub, assignment, state, meet_session)
                chat.update(receipt, controls["chat"])
                if assignment.get("voice_profiles") is True:
                    speech.update(receipt, controls, state["voice"])
                else:
                    speech.update(receipt, controls)
                if assignment.get("avatar_images") is True:
                    avatar.update(receipt, controls, state["avatar"])
                else:
                    avatar.update(receipt, controls)
                screen.update(
                    controls["screen"],
                    {
                        "chat": chat.opened is not None,
                        "reply": chat.pending is not None,
                        "audio": audio.stage if audio is not None and not audio.closed else "off",
                    },
                )
            chat.tick()
            speech.tick()
            screen.tick()
            avatar.tick()
            if audio is not None:
                try:
                    audio.tick()
                    if audio.closed:
                        audio = None
                except Exception:
                    audio.close()
                    audio = None
            # PCM is consumed in 20-ms frames. The old chat/screen idle cadence
            # can starve the deliberately small audio queue over browser RPC.
            page.wait_for_timeout(20 if speech.busy else 100)
        # Sources must stop even if normal leave never settles. ExitStack owns
        # exceptional cleanup and safely repeats these idempotent closes.
        if audio is not None:
            audio.close()
        screen.close()
        avatar.close()
        chat.close()
        speech.close()
        session.leave()


def main():
    # Drop the inherited seekable grant input before creating browser/model
    # children. Only the closed assignment in this delegated process remains.
    with sys.stdin.buffer as source:
        raw = source.read(MAX_DIALOG_BYTES + 1)
    assignment = validate_assignment(parse(raw), time.time())
    hub = HubDialogClient(assignment)
    status = "failed"
    try:
        run(assignment, hub)
        status = "completed"
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
