"""One Hub-assigned ephemeral browser. No human capture, profiles or task authority."""

import os
import sys
import time
from contextlib import ExitStack

from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES, parse, validate_assignment
from ananta_contracts.meet_reconnect import MAX_RECOVERIES
from ananta_contracts.meet_source_profile import dialog_source_profile
from worker.meet_media.browser_completion_reports import BrowserCompletionReports
from worker.meet_media.browser_media_timing import BrowserMediaTiming
from worker.meet_media.browser_network import restrict_meet_browser_network
from worker.meet_media.dialog_avatar_presentation import DialogAvatarPresentation
from worker.meet_media.dialog_avatar_pump import DialogAvatarPump
from worker.meet_media.dialog_browser_screen import DialogBrowserScreen
from worker.meet_media.dialog_chat import DialogChatPump
from worker.meet_media.dialog_chat import chat_scope_matches as chat_scope_matches
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_control_exchange import DialogControlExchange
from worker.meet_media.dialog_diagnostics import DialogRunDiagnostics
from worker.meet_media.dialog_diagnostics_deadline import bounded_terminal_report
from worker.meet_media.dialog_membership_loss import LOCAL_MEMBERSHIP, DialogMembershipLost, MembershipCheckpoint
from worker.meet_media.dialog_progress_channel import inherited_progress
from worker.meet_media.dialog_reconnect import reconnect_session
from worker.meet_media.dialog_screen_pump import DialogScreenPump
from worker.meet_media.dialog_session_binding import require_dialog_session
from worker.meet_media.dialog_session_operations import DialogSessionOperations
from worker.meet_media.dialog_speech_output import DialogSpeechOutput
from worker.meet_media.dialog_visual import start_visual


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

    try:
        delegated = hub.call("audio", meet_session_id=meet_session, publication_id=publication["publicationId"])
        return DialogAudioPump(page, hub, assignment, delegated["job"])
    except Exception:
        try:
            page.evaluate("window.anantaMachine.audio.close()")
        except Exception:
            pass  # A lost browser still fails the independent session/control loop.
        return None  # The Hub reservation bounds retries; no alternative source is inferred.


def run(assignment, hub, *, progress=None):
    # The fixed installed handler defines source classes. The signed closed v1
    # envelope carries capabilities/options, not a Worker-selected capture mode.
    dialog_source_profile(
        assignment["capabilities"],
        avatar_images=assignment.get("avatar_images", False),
        avatar_videos=assignment.get("avatar_videos", False),
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, ExitStack() as cleanup:
        browser = playwright.chromium.launch(
            headless=True, chromium_sandbox=True, args=["--autoplay-policy=no-user-gesture-required"]
        )
        cleanup.callback(browser.close)
        context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
        restrict_meet_browser_network(context, assignment["meeting"]["origin"])
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
        session.ready(
            assignment["capabilities"], **({"avatar_videos": True} if assignment.get("avatar_videos") is True else {})
        )
        session.join(assignment["meeting"]["room_id"], assignment["meeting"]["grant"])
        for recovery_count in range(MAX_RECOVERIES + 1):
            try:
                _run_joined(assignment, hub, page, browser, session, url=url, progress=progress)
            except DialogMembershipLost as lost:
                if assignment.get("reconnect") is not True or recovery_count >= MAX_RECOVERIES:
                    raise
                # All old pumps are closed by their own ExitStack before this
                # branch. A cleanup failure never reaches the grant handoff.
                session.leave()
                session = DialogSessionOperations(page, url=url, deadline=hub.deadline)
                cleanup.callback(session.close)
                reconnect_session(assignment, hub, page, session, lost.session_id, progress=progress)
            else:
                session.leave()
                return


def _run_joined(assignment, hub, page, browser, session, *, url, progress=None):
    checkpoint = MembershipCheckpoint(
        enabled=assignment.get("reconnect"),
        page=page,
        session=session,
        url=url,
        deadline=hub.deadline,
        clock=time.monotonic,
    )
    # Detect confirmed membership loss before cleanup. If cleanup itself fails,
    # its exception overrides recovery and the outer scope destroys the browser.
    with ExitStack() as cleanup, checkpoint.guard():
        meet_session = page.evaluate(LOCAL_MEMBERSHIP)["lease"]["sessionId"]
        speech = DialogSpeechOutput(
            page,
            assignment,
            **({"finished": hub.report_speech_finished} if assignment.get("speaker_floor") is True else {}),
        )
        cleanup.callback(speech.close)
        chat = DialogChatPump(page, hub, assignment, speech=speech)
        cleanup.callback(chat.close)
        if assignment.get("browser_workspace") is True:
            browser_reports = BrowserCompletionReports(hub)
            cleanup.callback(browser_reports.close)
            screen = DialogBrowserScreen(page, browser, assignment, finish=browser_reports.report)
        else:
            screen = DialogScreenPump(page, browser, assignment)
        cleanup.callback(screen.close)
        avatar = (
            DialogAvatarPresentation(page, hub, assignment)
            if assignment.get("avatar_images") is True
            else DialogAvatarPump(page, assignment)
        )
        cleanup.callback(avatar.close)
        audio = None
        visual = None
        # Resolve the current source at teardown, not an obsolete iteration's object.
        cleanup.callback(lambda: audio.close() if audio is not None else None)
        cleanup.callback(lambda: visual.close() if visual is not None else None)
        exchange = DialogControlExchange(hub, meet_session)
        cleanup.callback(exchange.close)
        control_revision = 0
        media_timing = None
        while time.monotonic() < hub.deadline:
            if page.url != url:
                raise ValueError("meet_machine_navigation_denied")
            state = exchange.poll(refresh_marker=chat.pending_refresh if chat.needs_refresh else None)
            if state is not None:
                receipt, controls = state["authorization"], state["controls"]
                local = page.evaluate(LOCAL_MEMBERSHIP)
                require_dialog_session(
                    local,
                    receipt,
                    controls,
                    room_id=assignment["meeting"]["room_id"],
                    previous_revision=control_revision,
                )
                checkpoint.confirm(receipt)
                control_revision = controls["revision"]
                if assignment.get("media_timing") is True and media_timing is None:
                    media_timing = BrowserMediaTiming(
                        page,
                        exchange.require_fresh,
                        assignment["capabilities"],
                        decoded_video=assignment.get("avatar_videos") is True,
                    )
                    cleanup.callback(media_timing.close)
                if progress is not None:
                    progress.report(exchange.fresh_until, hub.deadline)
                if state["renewal"]:
                    if visual is not None:
                        visual.close()
                        visual = None
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
                if visual is not None:
                    visual.refresh(receipt, state.get("visual_job"))
                else:
                    visual = start_visual(page, hub, assignment, state, meet_session)
                chat.update(receipt, controls["chat"])
                floor = {"speaker_floor": state["speaker_floor"]} if assignment.get("speaker_floor") is True else {}
                if assignment.get("voice_profiles") is True:
                    speech.update(receipt, controls, state["voice"], **floor)
                else:
                    speech.update(receipt, controls, **floor)
                if assignment.get("avatar_images") is True:
                    avatar.update(receipt, controls, state["avatar"])
                else:
                    avatar.update(receipt, controls)
                activity = {
                    "chat": chat.opened is not None,
                    "reply": chat.pending is not None,
                    "audio": audio.stage if audio is not None and not audio.closed else "off",
                }
                if assignment.get("browser_workspace") is True:
                    screen.update(receipt, controls["screen"], state["browser"], activity)
                else:
                    screen.update(controls["screen"], activity)
            if media_timing is not None:
                media_timing.poll()
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
            if visual is not None:
                try:
                    visual.tick()
                    if visual.closed:
                        visual = None
                except Exception:
                    visual.close()
                    visual = None
            # PCM is consumed in 20-ms frames. The old chat/screen idle cadence
            # can starve the deliberately small audio queue over browser RPC.
            page.wait_for_timeout(20 if speech.busy else 100)
        # Sources must stop even if normal leave never settles. ExitStack owns
        # exceptional cleanup and safely repeats these idempotent closes.
        if audio is not None:
            audio.close()
        if visual is not None:
            visual.close()
        screen.close()
        avatar.close()
        chat.close()
        speech.close()


def main():
    # Drop the inherited seekable grant input before creating browser/model
    # children. Only the closed assignment in this delegated process remains.
    with sys.stdin.buffer as source:
        raw = source.read(MAX_DIALOG_BYTES + 1)
    assignment = validate_assignment(parse(raw), time.time())
    hub = HubDialogClient(assignment)
    diagnostics = DialogRunDiagnostics(enabled=os.environ.get("MEET_DIALOG_DIAGNOSTICS_ENABLED") == "1")
    status = "failed"
    failure = None
    try:
        with inherited_progress() as progress:
            run(assignment, hub, progress=progress)
        status = "completed"
    except Exception as error:
        failure = error
        raise
    finally:
        try:
            hub.call("finish", status=status)
        except Exception:
            pass  # No contents, grants or upstream exception text in logs.
        finally:
            diagnostics.finish(
                lambda report: bounded_terminal_report(lambda: hub.report_terminal(report), hub.deadline),
                completed=status == "completed",
                failure=failure,
            )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
