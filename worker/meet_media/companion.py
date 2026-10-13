"""Persistent Ananta companion (ai-snake).

Stays in the room with one renewed machine session and a continuously published
synthetic snake avatar (always visible), reads the room chat, and answers each
user message with live speech (Piper PCM) plus a chat reply. Audio and avatar
clips are published from one shared media timeline (``companion_media``);
answers are routed and traced by ``companion_dialog`` so the snake can explain
how it produced them.
"""

import base64
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from worker.meet_media.avatar_idle_clip import IdleClipSource
from worker.meet_media.avatar_service import (
    LipSyncClient,
    health_report,
    load_portrait,
    portrait_path,
    service_enabled,
    service_url,
)
from worker.meet_media.companion_chat_diagnostics import (
    browser_console_diagnostic,
    chat_event_diagnostic,
    chat_state_diagnostic,
    renewal_status_diagnostic,
)
from worker.meet_media.companion_chat_inbox import CompanionChatInbox
from worker.meet_media.companion_chat_inbox_processing import serve_inbox
from worker.meet_media.companion_chat_ledger import ChatLedger
from worker.meet_media.companion_chat_pump import CompanionChatPump, CompanionSessionEnded
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_flags import (
    avatar_enabled,
    codecompass_enabled,
    rag_prefix_enabled,
    tools_enabled,
)
from worker.meet_media.companion_media import (
    FRAME_SAMPLES,
    SPEECH_RATE,
    AvatarPorts,
    IdleClips,
    SpeechAvatarPublisher,
)
from worker.meet_media.companion_media_process import CompanionMediaPreparationProcess
from worker.meet_media.companion_supervisor import CompanionLegacyRetired, supervise
from worker.meet_media.contract import encode, load_key, signature
from worker.meet_media.snake_avatar_state import IDLE, THINKING

ORIGIN = os.environ.get("MEET_ORIGIN", "https://webrtc.ananta.de")
PROJECT = os.environ.get("MEET_COMPANION_PROJECT", "ca1388ef-6be0-4d97-9f07-d7a7d877a89e")
HUB = os.environ.get(
    "MEET_HUB_COMPANION_URL", "http://meet-authorizing-hub:5000/api/meet/v1/internal/companion"
)
CAPABILITIES = ["avatar.publish", "speech.publish", "chat.send", "chat.read"]
DISPLAY_NAME = "ai-snake"
RENEW_INTERVAL = float(os.environ.get("MEET_COMPANION_RENEW", "40"))
# Refresh conservatively within the client's 120-s activation and initial Hub
# deadline. Parked publication reuse keeps reopening inexpensive.
AVATAR_REFRESH_SECONDS = float(os.environ.get("MEET_COMPANION_AVATAR_REFRESH", "25"))
STOP_FILE = Path(os.environ.get("MEET_COMPANION_STOP", "/state/companion-stop"))
LOG = open(os.environ.get("MEET_COMPANION_LOG", "/state/companion.log"), "a", buffering=1)


def log(message):
    LOG.write("%.3f %s\n" % (time.time(), message))


def decode_identity(grant):
    payload = grant.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return {"task_id": claims["taskId"], "runtime_id": claims["runtimeId"], "session_id": claims["sessionId"]}


ROOM_JSON = os.environ.get("MEET_COMPANION_ROOM_JSON", "/state/room.json")
PUBLIC_ROOM_URL = os.environ.get("MEET_HUB_PUBLIC_ROOM_URL") or HUB.rsplit("/", 1)[0] + "/public-room"
# Seconds between self-heal checks while joined; 0 checks only at join time.
PUBLIC_ROOM_CHECK = float(os.environ.get("MEET_COMPANION_PUBLIC_ROOM_CHECK", "300"))
ROOM_ID = re.compile(r"room-[a-f0-9]{18}")


def advertise_room(room_id, invite_url=None):
    """Persist the discoverable invite separately from content-free diagnostics."""
    url = invite_url or "%s/?room=%s&mode=room" % (ORIGIN, room_id)
    log("ROOM event=advertised")
    try:
        Path(ROOM_JSON).write_text(
            json.dumps({"room_id": room_id, "invite_url": url, "project": PROJECT}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:  # noqa: BLE001
        log("ROOM event=write_failed")


def fetch_grant(identity=None):
    key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
    body = encode({"project_id": PROJECT, "capabilities": CAPABILITIES, **(identity or {})})
    request = urllib.request.Request(
        HUB, body, {"Content-Type": "application/json", "X-Ananta-Task-Signature": signature(key, body)}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 410:
            raise CompanionLegacyRetired("meet_companion_legacy_retired_use_hub_tasks") from None
        raise


_PUBLIC_ROOM_DISABLED = False


def ensure_public_room():
    """Self-heal: have the Hub keep this project's public directory entry.

    The Hub reuses an entry that is still listed, re-publishes one turned
    private, or creates a new one and rebinds the project to it; the next grant
    then names that room. Never fatal: with the public room disabled or the
    room server unreachable the companion still joins the bound room.
    Returns ``{"room_id", "invite_url"}`` or None.
    """
    global _PUBLIC_ROOM_DISABLED
    if _PUBLIC_ROOM_DISABLED:
        return None
    try:
        key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
        body = encode({"project_id": PROJECT})
        request = urllib.request.Request(
            PUBLIC_ROOM_URL,
            body,
            {"Content-Type": "application/json", "X-Ananta-Task-Signature": signature(key, body)},
        )
        # The Hub fetches an operator token and reads the directory first.
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            code = (json.loads(error.read(4096) or b"{}").get("error") or {}).get("code")
        except (ValueError, AttributeError, OSError):
            code = None
        marker = code if code in (
            "meet_public_room_disabled", "meet_media_disabled", "meet_public_room_token_unavailable",
            "meet_public_room_directory_unavailable", "meet_public_room_unauthorized",
        ) else "unavailable"
        status = error.code if type(error.code) is int and 100 <= error.code <= 599 else "invalid"
        log("public_room_err status=%s code=%s" % (status, marker))
        if code in ("meet_public_room_disabled", "meet_media_disabled"):
            # Operator left it off: stop asking until the next process start.
            _PUBLIC_ROOM_DISABLED = True
        return None
    except Exception:  # noqa: BLE001
        log("public_room_err event=request_failed")
        return None
    room_id = result.get("roomId") if isinstance(result, dict) else None
    invite = result.get("inviteUrl") if isinstance(result, dict) else None
    if (
        not isinstance(room_id, str)
        or not ROOM_ID.fullmatch(room_id)
        or not isinstance(invite, str)
        or not invite.startswith("%s/?room=%s&" % (ORIGIN, room_id))
    ):
        log("public_room_err invalid_response")
        return None
    log("public_room event=resolved")
    return {"room_id": room_id, "invite_url": invite}


def public_invite(public, room_id):
    """Invite link for the joined room: the Hub's public link when it names that room."""
    if public is None:
        return None
    if public["room_id"] != room_id:
        # A concurrent rebind won; the grant stays authoritative for the join.
        log("public_room_mismatch event=binding_changed")
        return None
    return public["invite_url"]


AVATAR_PNG = os.environ.get("MEET_AVATAR_PNG", "/state/ananta-avatar.png")


def avatar_image():
    data = Path(AVATAR_PNG).read_bytes()
    return {"png": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest()}


def portrait_png():
    """Reference portrait asset for the lip-sync service (``MEET_AVATAR_SERVICE_PORTRAIT``).

    Never rendered here: the service detects the face from this exact image.
    A missing asset raises and disables lip-sync (local renderer fallback).
    """
    path = portrait_path()
    data = load_portrait(path)
    log("portrait %s bytes=%s sha256=%s" % (path, len(data), hashlib.sha256(data).hexdigest()[:12]))
    return data


_LIPSYNC = None


def lipsync_client():
    """Process-wide lip-sync client, or None when the service is disabled."""
    global _LIPSYNC
    if _LIPSYNC is None:
        _LIPSYNC = False
        if service_enabled():
            try:
                _LIPSYNC = LipSyncClient(portrait_png(), base_url=service_url(), log=log)
            except Exception as error:  # noqa: BLE001
                log("lipsync disabled %r" % (error,))
    return _LIPSYNC or None


def log_lipsync_service():
    if not service_enabled():
        log("lipsync service disabled")
        return
    try:
        report = health_report(service_url()) or {}
        log("lipsync service=%s ready=%s last_face_method=%s" % (
            service_url(), report.get("ready") is True, (report.get("last_inference") or {}).get("face_method")))
    except Exception as error:  # noqa: BLE001
        log("lipsync service_err %r" % (error,))


# Pre-rendered idle loop (MEET_AVATAR_IDLE_CLIP); the procedural renderer only
# answers when the asset is unusable.
_IDLE_CLIPS = IdleClipSource(fallback=IdleClips(), log=log)


def avatar_idle(state=IDLE):
    """Looping snake clip for a non-speaking state (gentle breathing, mouth closed)."""
    return _IDLE_CLIPS.payload(state)


def avatar_speaking(pcm):
    """Compatibility: first speaking clip whose mouth follows the reply audio."""
    publisher = SpeechAvatarPublisher(AvatarPorts(None, None, None, None))
    _timeline, clips = publisher.prepare(pcm)
    return clips[0]


_DIALOG = None
_TOOLBOX = None


def dialog():
    """Process-wide companion dialog (router + CodeCompass + trace).

    CodeCompass reaches the model two ways, chosen by flags: as a tool the
    model calls itself (``MEET_COMPANION_TOOLS``, default on) and/or as the
    previous prefix of retrieved snippets (``MEET_COMPANION_RAG_PREFIX``,
    default off). The knowledge flag still gates both.
    """
    global _DIALOG, _TOOLBOX
    if _DIALOG is None:
        from worker.meet_media.assist import call_tool, fetch_snippets, retrieve_snippets
        from worker.meet_media.companion_dialog import (
            CompanionDialog,
            persona_for,
        )
        from worker.meet_media.llm import answer
        from worker.meet_media.llm_tools import codecompass_toolbox

        knowledge = codecompass_enabled()
        tools = knowledge and tools_enabled()
        prefix = knowledge and rag_prefix_enabled()
        log("flags codecompass=%s tools=%s rag_prefix=%s" % (knowledge, tools, prefix))
        # Every CodeCompass tool runs on the Hub over the worker key
        # (/internal/assist/tool); codecompass_search is codecompass.retrieve.
        _TOOLBOX = (
            codecompass_toolbox(
                lambda query, limit: retrieve_snippets(query, limit=limit, project=PROJECT),
                lambda tool, arguments: call_tool(tool, arguments, project=PROJECT),
            )
            if tools
            else None
        )
        _DIALOG = CompanionDialog(
            llm=lambda text, context, system: answer(text, context=context, system=system, tools=_TOOLBOX),
            # Decision fast path (MEET_TOOL_DECISION_FAST): a confident tool call is forced like the router's.
            tool_choice=tool_decisions()[0] if _TOOLBOX is not None else None,
            # Decision routing (MEET_ROUTE_DECISION shadow/active) next to the keyword rules.
            routing=route_decisions(),
            retriever=(lambda query: fetch_snippets(query, limit=5)) if prefix else None,
            codecompass_enabled=prefix,
            model_name=os.environ.get("MEET_LLM_MODEL", ""),
            system=persona_for(_TOOLBOX),
            # Knowledge questions get codecompass_search forced by the router.
            tools=_TOOLBOX,
        )
    return _DIALOG


_ROUTING = None
_ROUTING_BUILT = False


def route_decisions():
    """The routing port from ``MEET_ROUTE_DECISION`` (built once), or ``None``."""
    global _ROUTING, _ROUTING_BUILT
    if not _ROUTING_BUILT:
        from worker.meet_media.route_decision import routing_from_env

        _ROUTING = routing_from_env()
        _ROUTING_BUILT = True
    return _ROUTING


def generate_reply(text):
    conversation = dialog()
    toolbox = _TOOLBOX
    fast, shadow = tool_decisions()
    if fast is not None:
        fast.last = None
    if toolbox is not None:
        toolbox.reset()
    reply, trace = conversation.answer(text)
    if toolbox is not None:
        # What the model actually looked up belongs in the same trace the snake
        # explains itself from, so "wie hast du das erzeugt?" stays truthful.
        for call in toolbox.calls:
            name = call.get("tool") or "codecompass_search"
            trace.codecompass_used = True
            trace.observe(
                "%s %s (query=%s) -> %d Auszug/Auszüge%s"
                % (
                    name,
                    "vom Router erzwungen" if call.get("forced") else "aufgerufen",
                    call["query"][:60],
                    call["snippets"],
                    ", fehlgeschlagen" if call["failed"] else "",
                )
            )
            log("TOOL %s query=%r snippets=%s total=%s failed=%s code=%s forced=%s"
                % (name, call["query"][:80], call["snippets"], call.get("total"), call["failed"],
                   call.get("code", ""), bool(call.get("forced"))))
        trace.add_sources(toolbox.sources)
    log("TRACE route=%s tools=%s sources=%s" % (
        trace.route, len(toolbox.calls) if toolbox is not None else 0, trace.source_labels()[:4]))
    if shadow is not None and toolbox is not None:
        if fast is not None and fast.last is not None:
            # The fast path already decided this question: record that decision, no second call.
            shadow.record(text, fast.last, route=trace.route, calls=toolbox.calls, mode="fast")
        else:
            # Record only; runs after the reply is final and never changes it.
            shadow.observe_later(text, toolbox.definitions(), route=trace.route, calls=toolbox.calls)
    return reply


_TOOL_DECISIONS = None


def tool_decisions():
    """``(fast path, shadow)`` of the decision-based tool choice; each ``None`` when off. One decider serves both."""
    global _TOOL_DECISIONS
    if _TOOL_DECISIONS is None:
        from worker.meet_media.tool_decision import decider_from_env, fast_choice_from_env
        from worker.meet_media.tool_decision_shadow import from_env

        decider = decider_from_env()
        _TOOL_DECISIONS = (fast_choice_from_env(decider), from_env(decider=decider))
        log("tool decision fast=%s shadow=%s" % tuple("on" if item is not None else "off" for item in _TOOL_DECISIONS))
    return _TOOL_DECISIONS


def synthesize_pcm(reply):
    """Return mono s16le PCM at SPEECH_RATE for the reply."""
    from worker.meet_media.piper_speech import PiperSpeechSource
    from worker.meet_media.speech import speech

    with tempfile.TemporaryDirectory(prefix="companion-") as temporary:
        wav = Path(temporary) / "speech.wav"
        speech(reply, wav, source=PiperSpeechSource(), max_seconds=40)
        data = wav.read_bytes()
        start = data.index(b"data") + 8
        return data[start:]


def avatar_ports(page, avatar_source, speech_source):
    """Narrow browser ports for the shared-timeline publisher."""

    def avatar_open(payload):
        receipt = page.evaluate(
            "(a) => window.anantaMachine.avatar.open(a[0], 'persona-video-v1', a[1])", [avatar_source, payload]
        )
        page.evaluate("(g) => { window.__avatarGen = g; }", receipt["generation"])
        return receipt["generation"]

    def avatar_close(generation):
        page.evaluate("(g) => window.anantaMachine.avatar.close(g)", generation)

    def speech_open(total):
        receipt = page.evaluate("(a) => window.anantaMachine.speech.open(a[0], a[1])", [speech_source, total])
        log("speech open gen=%s samples=%s" % (receipt["generation"], total))
        return receipt["generation"]

    def speech_push(generation, offset, chunk):
        page.evaluate(
            "(a) => window.anantaMachine.speech.push(a[0], a[1], a[2])",
            [generation, offset, base64.b64encode(chunk).decode()],
        )

    def speech_status():
        return page.evaluate("() => window.anantaMachine.speech.status()")

    return AvatarPorts(avatar_open, avatar_close, speech_open, speech_push, speech_status=speech_status)


def speak(page, source_id, pcm, *, ports=None, avatar_generation=None, cooperate=None, clip_preparer=None):
    """Publish speech (and, when the avatar is enabled, synced clips)."""
    if ports is None or not avatar_enabled():
        total = len(pcm) // 2
        receipt = page.evaluate("(a) => window.anantaMachine.speech.open(a[0], a[1])", [source_id, total])
        generation = receipt["generation"]
        log("speech open gen=%s samples=%s" % (generation, total))
        started = time.monotonic()
        for offset in range(0, total, FRAME_SAMPLES):
            if cooperate is not None:
                cooperate()
            chunk = pcm[offset * 2:(offset + FRAME_SAMPLES) * 2]
            page.evaluate(
                "(a) => window.anantaMachine.speech.push(a[0], a[1], a[2])",
                [generation, offset, base64.b64encode(chunk).decode()],
            )
            target = started + (offset / SPEECH_RATE) - 0.12
            delay = target - time.monotonic()
            if delay > 0:
                (ports.sleep if ports is not None else time.sleep)(delay)
        return avatar_generation
    publisher = SpeechAvatarPublisher(ports, log=log, clip_preparer=clip_preparer,
                                     lipsync=None if clip_preparer is not None else lipsync_client())
    try:
        generation, report = publisher.speak(pcm, current_generation=avatar_generation)
        log("SYNC max_drift_us=%s segments=%s" % (report["max_drift_us"], len(report["segments"])))
        return generation
    except ValueError as error:
        # Drift beyond the bound is reported, never hidden; the clip generation
        # stays valid so the tile does not drop.
        log("SYNC_ERR %s" % (error,))
        return publisher.generation if publisher.generation is not None else avatar_generation


def show_state(ports, generation, state, enabled=True, *, idle_payload=None):
    """Swap the idle clip for ``state``; returns the new avatar generation."""
    if not enabled:
        return generation
    try:
        payload = (idle_payload or avatar_idle)(state)
        if generation is not None:
            ports.avatar_close(generation)
        generation = ports.avatar_open(payload)
        log("avatar state=%s gen=%s" % (state, generation))
    except Exception as error:  # noqa: BLE001
        log("avatar_state_err %r" % (error,))
    return generation


def run():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, chromium_sandbox=True, args=["--autoplay-policy=no-user-gesture-required"]
        )
        execution = None
        media_preparation = None
        try:
            context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
            page = context.new_page()
            page.set_default_timeout(15000)

            def _on_response(response):
                url = response.url
                if "/api/machine/sessions/renew" in url:
                    try:
                        log(renewal_status_diagnostic(response.status))
                    except Exception:  # noqa: BLE001
                        log("RENEW event=observation_failed")

            def _on_console(message):
                try:
                    kind = message.type
                    marker = browser_console_diagnostic(
                        kind, e2ee=kind == "log" and "[e2eedbg]" in str(message.text)[:1200]
                    )
                    if marker is not None:
                        log(marker)
                except Exception:  # noqa: BLE001
                    log("CONSOLE event=observation_failed")

            page.on("response", _on_response)
            page.on("console", _on_console)
            page.goto(ORIGIN + "/machine", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.anantaMachine && typeof window.anantaMachine.join === 'function'", timeout=30000
            )
            public = ensure_public_room()
            grant = fetch_grant()
            identity = decode_identity(grant["grant"])
            avatar_source = "avatar:" + identity["session_id"]
            speech_source = "speech:" + identity["session_id"]
            ports = avatar_ports(page, avatar_source, speech_source)
            avatar_on = avatar_enabled()
            log("flags avatar=%s codecompass=%s tools=%s rag_prefix=%s" % (
                avatar_on, codecompass_enabled(), tools_enabled(), rag_prefix_enabled()))
            log_lipsync_service()
            log("grant event=received")
            room_id = grant["room_id"]
            advertise_room(room_id, public_invite(public, room_id))
            page.evaluate("(a) => window.anantaMachine.join(a[0], a[1])", [grant["room_id"], grant["grant"]])
            for _ in range(30):
                state = page.evaluate("() => window.anantaMachine.status()")
                if state and state.get("joined"):
                    break
                time.sleep(1)
            log("joined " + chat_state_diagnostic(page.evaluate("() => window.anantaMachine.status()")))
            # Keep the avatar controller alive across bounded browser/media
            # setup calls as well as the owner's cooperative execution waits.
            page.evaluate("""() => {
              if (!window.__avatarTimer) {
                window.__avatarTimer = setInterval(() => {
                  try { if (window.__avatarGen) window.anantaMachine.avatar.pulse(window.__avatarGen); }
                  catch (error) { window.__avatarGen = 0; }
                }, 1500);
              }
            }""")
            last_status_log = 0.0
            ledger = ChatLedger()

            inbox = CompanionChatInbox()
            execution = CompanionExecutionProcess()
            media_preparation = CompanionMediaPreparationProcess()

            def renew_chat_session():
                fresh = fetch_grant(identity)
                if fresh["room_id"] != room_id or decode_identity(fresh["grant"]) != identity:
                    raise ValueError("meet_companion_renew_binding_changed")
                page.evaluate("(g) => window.anantaMachine.renew(g)", fresh["grant"])

            pump = CompanionChatPump(
                inbox, status=lambda: page.evaluate("() => window.anantaMachine.status()"),
                open_chat=lambda: page.evaluate("() => window.anantaMachine.chat.open()"),
                poll=lambda cursor: page.evaluate("(c) => window.anantaMachine.chat.poll(c)", cursor),
                redeliver=lambda ids: page.evaluate("(ids) => window.anantaMachine.chat.redeliver(ids)", ids),
                reply_pending=lambda identity: identity not in ledger.answered,
                renew=renew_chat_session, stopped=STOP_FILE.exists, log=log, renew_interval=RENEW_INTERVAL,
            )

            def cooperative_sleep(seconds):
                deadline = time.monotonic() + seconds
                while True:
                    pump.step()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    time.sleep(min(remaining, 0.1))

            ports = replace(ports, sleep=cooperative_sleep)

            def prepare_idle(state):
                return media_preparation.idle(state, cooperate=pump.step)

            def prepare_speaking(pcm):
                return media_preparation.speaking(pcm, cooperate=pump.step)
            last_public_check = time.time()
            outcome = None
            avatar_generation = None
            avatar_opened = 0.0
            while not STOP_FILE.exists():
                now = time.time()
                try:
                    state = pump.step()
                except CompanionSessionEnded as ended:
                    log("left" if str(ended) == "meet_companion_session_lost" else "session_ended")
                    break
                if now - last_status_log > 10:
                    last_status_log = now
                    log(chat_state_diagnostic(state))
                # Reconcile with the client's chat state: answers already shown
                # count as answered; delivered-never inputs are reported by ID.
                ledger.observe(state.get("chat"), delivered_message_ids=inbox.pending_message_ids())
                for missing in ledger.gaps():
                    log("chat_gap id=%s undelivered" % missing[:8])

                # Keep the synthetic avatar published so the tile is always visible.
                # The open itself creates the camera publication that drives the
                # media-key handshake, so it must run even while e2ee is pending;
                # the client-side setup timeout gives the handshake time to land.
                if avatar_on and (avatar_generation is None or now - avatar_opened > AVATAR_REFRESH_SECONDS):
                    try:
                        avatar_generation = show_state(ports, avatar_generation, IDLE, idle_payload=prepare_idle)
                    except CompanionSessionEnded:
                        break
                    avatar_opened = now
                if inbox.batch()["events"]:
                    def answer(event, deliver):
                        nonlocal avatar_generation, avatar_opened
                        text = str(event.get("text", "") or "")
                        log(chat_event_diagnostic("RECV", event))
                        avatar_generation = show_state(ports, avatar_generation, THINKING, avatar_on,
                                                       idle_payload=prepare_idle)
                        reply = execution.call("answer", text, cooperate=pump.step)
                        log(chat_event_diagnostic("REPLY", event))
                        deliver(reply)
                        pcm = execution.call("speech", reply, cooperate=pump.step)
                        avatar_generation = speak(
                            page, speech_source, pcm, ports=ports, avatar_generation=avatar_generation,
                            cooperate=pump.step,
                            clip_preparer=prepare_speaking,
                        )
                        avatar_opened = time.time()
                        avatar_generation = show_state(ports, avatar_generation, IDLE, avatar_on,
                                                       idle_payload=prepare_idle)

                    try:
                        serve_inbox(
                            inbox, ledger,
                            send=lambda message_id, reply: page.evaluate(
                                "(a) => window.anantaMachine.chat.reply(a[0], a[1])", [message_id, reply]
                            ),
                            ack=lambda cursor: page.evaluate("(c) => window.anantaMachine.chat.ack(c)", cursor),
                            answer=answer, log=log,
                        )
                    except CompanionSessionEnded:
                        break

                if PUBLIC_ROOM_CHECK > 0 and now - last_public_check > PUBLIC_ROOM_CHECK:
                    last_public_check = now
                    public = ensure_public_room()
                    if public is not None and public["room_id"] != room_id:
                        # The directory entry was lost and re-created: the
                        # binding now names a new room; follow it there.
                        log("ROOM_MOVED event=binding_changed")
                        outcome = "moved"
                        break
                time.sleep(0.5)
            try:
                page.evaluate("() => window.anantaMachine.leave()")
            except Exception:  # noqa: BLE001
                pass
            return outcome
        finally:
            try:
                if execution is not None:
                    execution.close()
            finally:
                try:
                    if media_preparation is not None:
                        media_preparation.close()
                finally:
                    browser.close()


def main():
    """Run the companion; every lost session re-joins with a fresh Hub grant."""
    supervise(run, STOP_FILE.exists, clock=time.time, sleep=time.sleep, log=log)


if __name__ == "__main__":
    main()
