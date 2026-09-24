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
import tempfile
import time
import urllib.request
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
from worker.meet_media.companion_chat_ledger import ChatLedger, port_closed, serve_batch
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
# The client expires an avatar activation after ~30 s; reopening the idle clip
# below that keeps the tile continuous (the publication itself is parked, so
# a reopen is cheap).
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


def advertise_room(room_id):
    """Log and persist the room id + invite url so it can be found without the log."""
    url = "%s/?room=%s&mode=room" % (ORIGIN, room_id)
    log("ROOM %s %s" % (room_id, url))
    try:
        Path(ROOM_JSON).write_text(
            json.dumps({"room_id": room_id, "invite_url": url, "project": PROJECT}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:  # noqa: BLE001
        log("room_write_err %r" % (error,))


def fetch_grant(identity=None):
    key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
    body = encode({"project_id": PROJECT, "capabilities": CAPABILITIES, **(identity or {})})
    request = urllib.request.Request(
        HUB, body, {"Content-Type": "application/json", "X-Ananta-Task-Signature": signature(key, body)}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


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
        from worker.meet_media.assist import fetch_snippets
        from worker.meet_media.companion_dialog import (
            PERSONA_SYSTEM,
            PERSONA_SYSTEM_WITH_TOOLS,
            CompanionDialog,
        )
        from worker.meet_media.llm import answer
        from worker.meet_media.llm_tools import codecompass_toolbox

        knowledge = codecompass_enabled()
        tools = knowledge and tools_enabled()
        prefix = knowledge and rag_prefix_enabled()
        log("flags codecompass=%s tools=%s rag_prefix=%s" % (knowledge, tools, prefix))
        _TOOLBOX = codecompass_toolbox(lambda query, limit: fetch_snippets(query, limit=limit)) if tools else None
        _DIALOG = CompanionDialog(
            llm=lambda text, context, system: answer(text, context=context, system=system, tools=_TOOLBOX),
            retriever=(lambda query: fetch_snippets(query, limit=5)) if prefix else None,
            codecompass_enabled=prefix,
            model_name=os.environ.get("MEET_LLM_MODEL", ""),
            system=PERSONA_SYSTEM_WITH_TOOLS if tools else PERSONA_SYSTEM,
            # Knowledge questions get codecompass_search forced by the router.
            tools=_TOOLBOX,
        )
    return _DIALOG


def generate_reply(text):
    conversation = dialog()
    toolbox = _TOOLBOX
    if toolbox is not None:
        toolbox.reset()
    reply, trace = conversation.answer(text)
    if toolbox is not None:
        # What the model actually looked up belongs in the same trace the snake
        # explains itself from, so "wie hast du das erzeugt?" stays truthful.
        for call in toolbox.calls:
            trace.codecompass_used = True
            trace.observe(
                "codecompass_search %s (query=%s) -> %d Auszug/Auszüge"
                % ("vom Router erzwungen" if call.get("forced") else "aufgerufen", call["query"][:60], call["snippets"])
            )
            log("TOOL codecompass_search query=%r snippets=%s failed=%s forced=%s"
                % (call["query"][:80], call["snippets"], call["failed"], bool(call.get("forced"))))
        trace.add_sources(toolbox.sources)
    log("TRACE route=%s tools=%s sources=%s" % (
        trace.route, len(toolbox.calls) if toolbox is not None else 0, trace.source_labels()[:4]))
    return reply


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


def speak(page, source_id, pcm, *, ports=None, avatar_generation=None):
    """Publish speech (and, when the avatar is enabled, synced clips)."""
    if ports is None or not avatar_enabled():
        total = len(pcm) // 2
        receipt = page.evaluate("(a) => window.anantaMachine.speech.open(a[0], a[1])", [source_id, total])
        generation = receipt["generation"]
        log("speech open gen=%s samples=%s" % (generation, total))
        started = time.monotonic()
        for offset in range(0, total, FRAME_SAMPLES):
            chunk = pcm[offset * 2:(offset + FRAME_SAMPLES) * 2]
            page.evaluate(
                "(a) => window.anantaMachine.speech.push(a[0], a[1], a[2])",
                [generation, offset, base64.b64encode(chunk).decode()],
            )
            target = started + (offset / SPEECH_RATE) - 0.12
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return avatar_generation
    publisher = SpeechAvatarPublisher(ports, log=log, lipsync=lipsync_client())
    try:
        generation, report = publisher.speak(pcm, current_generation=avatar_generation)
        log("SYNC max_drift_us=%s segments=%s" % (report["max_drift_us"], len(report["segments"])))
        return generation
    except ValueError as error:
        # Drift beyond the bound is reported, never hidden; the clip generation
        # stays valid so the tile does not drop.
        log("SYNC_ERR %s" % (error,))
        return publisher.generation if publisher.generation is not None else avatar_generation


def show_state(ports, generation, state, enabled=True):
    """Swap the idle clip for ``state``; returns the new avatar generation."""
    if not enabled:
        return generation
    try:
        if generation is not None:
            ports.avatar_close(generation)
        generation = ports.avatar_open(avatar_idle(state))
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
        try:
            context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
            page = context.new_page()
            page.set_default_timeout(15000)

            def _on_response(response):
                url = response.url
                if "/api/machine/sessions/renew" in url:
                    try:
                        log("RENEW %s %s" % (response.status, response.text()[:300]))
                    except Exception as error:  # noqa: BLE001
                        log("RENEWERR %r" % (error,))

            def _on_console(message):
                text = str(message.text)[:1200]
                if "[e2eedbg]" in text or message.type in ("error", "warning"):
                    log("CONSOLE %s %s" % (message.type, text))

            page.on("response", _on_response)
            page.on("console", _on_console)
            page.goto(ORIGIN + "/machine", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.anantaMachine && typeof window.anantaMachine.join === 'function'", timeout=30000
            )
            grant = fetch_grant()
            identity = decode_identity(grant["grant"])
            avatar_source = "avatar:" + identity["session_id"]
            speech_source = "speech:" + identity["session_id"]
            ports = avatar_ports(page, avatar_source, speech_source)
            avatar_on = avatar_enabled()
            log("flags avatar=%s codecompass=%s tools=%s rag_prefix=%s" % (
                avatar_on, codecompass_enabled(), tools_enabled(), rag_prefix_enabled()))
            log_lipsync_service()
            log("grant room=%s exp=%s" % (grant["room_id"], grant.get("expires_at")))
            advertise_room(grant["room_id"])
            page.evaluate("(a) => window.anantaMachine.join(a[0], a[1])", [grant["room_id"], grant["grant"]])
            for _ in range(30):
                state = page.evaluate("() => window.anantaMachine.status()")
                if state and state.get("joined"):
                    break
                time.sleep(1)
            log("joined=%s" % json.dumps(page.evaluate("() => window.anantaMachine.status()"))[:260])
            # Keep the avatar controller alive from inside the page: the Python
            # loop blocks during LLM/TTS, which would starve the 2.5s heartbeat.
            page.evaluate("""() => {
              if (!window.__avatarTimer) {
                window.__avatarTimer = setInterval(() => {
                  try { if (window.__avatarGen) window.anantaMachine.avatar.pulse(window.__avatarGen); }
                  catch (error) { window.__avatarGen = 0; }
                }, 1500);
              }
            }""")
            chat_opened = False
            last_chat_attempt = 0.0
            last_status_log = 0.0
            ledger = ChatLedger()

            last_seen = 0
            last_renew = time.time()
            avatar_generation = None
            avatar_opened = 0.0
            while not STOP_FILE.exists():
                now = time.time()
                try:
                    state = page.evaluate("() => window.anantaMachine.status()")
                except Exception as error:  # noqa: BLE001
                    log("status_err %r" % (error,))
                    break
                if not state or not state.get("joined"):
                    log("left")
                    break
                if now - last_status_log > 10:
                    last_status_log = now
                    log("STATE peers=%s e2ee=%s chat=%s %s" % (
                        state.get("peers"), state.get("e2ee"), len(state.get("chat") or []),
                        json.dumps(state.get("chat") or [], default=str)[:400]))
                # Reconcile with the client's chat state: answers already shown
                # count as answered; delivered-never inputs are reported by ID.
                ledger.observe(state.get("chat"))
                for missing in ledger.gaps():
                    log("chat_gap id=%s undelivered" % missing[:8])

                # Keep the synthetic avatar published so the tile is always visible.
                # The open itself creates the camera publication that drives the
                # media-key handshake, so it must run even while e2ee is pending;
                # the client-side setup timeout gives the handshake time to land.
                if avatar_on and (avatar_generation is None or now - avatar_opened > AVATAR_REFRESH_SECONDS):
                    try:
                        if avatar_generation is not None:
                            ports.avatar_close(avatar_generation)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        avatar_generation = ports.avatar_open(avatar_idle(IDLE))
                        avatar_opened = now
                        log("avatar open gen=%s" % avatar_generation)
                    except Exception as error:  # noqa: BLE001
                        log("avatar_open_err %r" % (error,))
                        avatar_generation = None
                if not chat_opened and now - last_chat_attempt > 3:
                    last_chat_attempt = now
                    try:
                        page.evaluate("() => window.anantaMachine.chat.open()")
                        chat_opened = True
                        log("chat opened")
                    except Exception as error:  # noqa: BLE001
                        log("chat_open_err %r" % (error,))

                if chat_opened:
                    try:
                        batch = page.evaluate("() => window.anantaMachine.chat.poll()")
                    except Exception as error:  # noqa: BLE001
                        log("chat_poll_err %r" % (error,))
                        batch = None
                        # The chat queue closes on session renewal (lease
                        # generation changes). Reopen at once: the client holds
                        # unacknowledged inputs only until the next open().
                        chat_opened = False
                        if port_closed(error):
                            last_chat_attempt = 0.0
                    if batch and batch.get("events"):

                        def answer(event, deliver):
                            nonlocal avatar_generation, avatar_opened
                            text = str(event.get("text", "") or "")
                            log("RECV %s: %s" % (event.get("sender_peer_id", ""), text[:140]))
                            # thinking -> speaking -> idle: the state is
                            # visible while the model and TTS are working.
                            avatar_generation = show_state(ports, avatar_generation, THINKING, avatar_on)
                            reply = generate_reply(text)
                            log("REPLY %s" % reply[:140])
                            deliver(reply)
                            pcm = synthesize_pcm(reply)
                            avatar_generation = speak(
                                page, speech_source, pcm, ports=ports, avatar_generation=avatar_generation
                            )
                            avatar_opened = time.time()
                            avatar_generation = show_state(ports, avatar_generation, IDLE, avatar_on)

                        serve_batch(
                            batch,
                            ledger,
                            send=lambda message_id, reply: page.evaluate(
                                "(a) => window.anantaMachine.chat.reply(a[0], a[1])", [message_id, reply]
                            ),
                            # The batch has no top-level cursor: ACK the highest
                            # delivered one, only after the inputs were handled.
                            ack=lambda cursor: page.evaluate("(c) => window.anantaMachine.chat.ack(c)", cursor),
                            answer=answer,
                            log=log,
                        )

                if now - last_renew > RENEW_INTERVAL:
                    try:
                        fresh = fetch_grant(identity)
                        page.evaluate("(g) => window.anantaMachine.renew(g)", fresh["grant"])
                        log("renewed exp=%s" % fresh.get("expires_at"))
                    except Exception as error:  # noqa: BLE001
                        log("renew_err %r" % (error,))
                    last_renew = time.time()
                time.sleep(0.5)
            try:
                page.evaluate("() => window.anantaMachine.leave()")
            except Exception:  # noqa: BLE001
                pass
        finally:
            browser.close()


if __name__ == "__main__":
    run()