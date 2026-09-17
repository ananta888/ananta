"""Persistent Ananta companion.

Stays in the room with one renewed machine session and a continuously published
synthetic avatar (always visible), reads the room chat, and answers each user
message with live speech (Piper PCM) plus a chat reply.
"""

import base64
import hashlib
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path

from worker.meet_media.contract import encode, load_key, signature

ORIGIN = os.environ.get("MEET_ORIGIN", "https://webrtc.ananta.de")
PROJECT = os.environ.get("MEET_COMPANION_PROJECT", "ca1388ef-6be0-4d97-9f07-d7a7d877a89e")
HUB = os.environ.get(
    "MEET_HUB_COMPANION_URL", "http://meet-authorizing-hub:5000/api/meet/v1/internal/companion"
)
CAPABILITIES = ["avatar.publish", "speech.publish", "chat.send", "chat.read"]
DISPLAY_NAME = "ai-snake"
RENEW_INTERVAL = float(os.environ.get("MEET_COMPANION_RENEW", "40"))
SPEECH_RATE = 22050
FRAME_SAMPLES = 441
STOP_FILE = Path(os.environ.get("MEET_COMPANION_STOP", "/state/companion-stop"))
LOG = open(os.environ.get("MEET_COMPANION_LOG", "/state/companion.log"), "a", buffering=1)


def log(message):
    LOG.write("%.3f %s\n" % (time.time(), message))


def decode_identity(grant):
    payload = grant.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return {"task_id": claims["taskId"], "runtime_id": claims["runtimeId"], "session_id": claims["sessionId"]}


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


_IDLE_VIDEO = None


def _snake_video(samples, seconds, repeat):
    import numpy as np

    from worker.meet_media.snake_avatar import build_video, video_payload

    with tempfile.TemporaryDirectory(prefix="snake-") as temporary:
        data, frames = build_video(np.asarray(samples, dtype=np.float32), SPEECH_RATE, seconds, temporary)
    return video_payload(data, frames, repeat=repeat)


def avatar_idle():
    """Looping snake clip with a closed, smiling mouth."""
    global _IDLE_VIDEO
    if _IDLE_VIDEO is None:
        import numpy as np

        _IDLE_VIDEO = _snake_video(np.zeros(SPEECH_RATE, dtype=np.float32), 2.0, "loop")
    return _IDLE_VIDEO


def avatar_speaking(pcm):
    """Snake clip whose mouth follows the reply audio envelope."""
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    seconds = min(10.0, max(0.25, len(samples) / SPEECH_RATE))
    return _snake_video(samples, seconds, "hold_last")


def generate_reply(text):
    from worker.meet_media.assist import build_context
    from worker.meet_media.llm import answer

    return answer(text, context=build_context(text))


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


def speak(page, source_id, pcm):
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
        # Keep ~120ms of lead over real-time playback: enough to survive jitter,
        # within the bounded queue (never "meet_speech_buffer_exceeded").
        target = started + (offset / SPEECH_RATE) - 0.12
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
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
                text = str(message.text)[:300]
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
            log("grant room=%s exp=%s" % (grant["room_id"], grant.get("expires_at")))
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
            processed = set()

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

                # Keep the synthetic avatar published so the tile is always visible.
                if avatar_generation is None or now - avatar_opened > 90:
                    try:
                        if avatar_generation is not None:
                            page.evaluate("(g) => window.anantaMachine.avatar.close(g)", avatar_generation)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        receipt = page.evaluate(
                            "(a) => window.anantaMachine.avatar.open(a[0], 'persona-video-v1', a[1])",
                            [avatar_source, avatar_idle()],
                        )
                        avatar_generation = receipt["generation"]
                        avatar_opened = now
                        page.evaluate("(g) => { window.__avatarGen = g; }", avatar_generation)
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
                        # generation changes); reopen it on the next loop.
                        chat_opened = False
                    if batch and batch.get("events"):
                        for item in batch["events"]:
                            event = item.get("event", {}) or {}
                            text = str(event.get("text", "") or "")
                            message_id = str(event.get("message_id", "") or "")
                            sender = str(event.get("sender_peer_id", "") or "")
                            if not text or not message_id or message_id in processed:
                                continue
                            processed.add(message_id)
                            if len(processed) > 512:
                                processed.clear()
                            log("RECV %s: %s" % (sender, text[:140]))
                            try:
                                reply = generate_reply(text)
                                log("REPLY %s" % reply[:140])
                                try:
                                    page.evaluate(
                                        "(a) => window.anantaMachine.chat.reply(a[0], a[1])", [message_id, reply]
                                    )
                                except Exception as error:  # noqa: BLE001
                                    log("chat_reply_err %r" % (error,))
                                pcm = synthesize_pcm(reply)
                                try:
                                    if avatar_generation is not None:
                                        page.evaluate("(g) => window.anantaMachine.avatar.close(g)", avatar_generation)
                                    receipt = page.evaluate(
                                        "(a) => window.anantaMachine.avatar.open(a[0], 'persona-video-v1', a[1])",
                                        [avatar_source, avatar_speaking(pcm)],
                                    )
                                    avatar_generation = receipt["generation"]
                                    avatar_opened = time.time()
                                    page.evaluate("(g) => { window.__avatarGen = g; }", avatar_generation)
                                except Exception as error:  # noqa: BLE001
                                    log("avatar_talk_err %r" % (error,))
                                speak(page, speech_source, pcm)
                            except Exception as error:  # noqa: BLE001
                                log("reply_err %r" % (error,))
                        try:
                            page.evaluate("(c) => window.anantaMachine.chat.ack(c)", batch.get("cursor"))
                        except Exception as error:  # noqa: BLE001
                            log("chat_ack_err %r" % (error,))

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