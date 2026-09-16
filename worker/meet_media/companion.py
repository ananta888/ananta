"""Persistent Ananta companion.

Stays in the room with one renewed machine session and a continuously published
synthetic avatar (always visible), reads the room chat, and answers each user
message with live speech (Piper PCM) plus a chat reply.
"""

import base64
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
DISPLAY_NAME = "Ananta (KI)"
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


def generate_reply(text):
    from worker.meet_media.llm import answer

    return answer(text)


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
    for start in range(0, total, FRAME_SAMPLES):
        chunk = pcm[start * 2:(start + FRAME_SAMPLES) * 2]
        page.evaluate(
            "(a) => window.anantaMachine.speech.push(a[0], a[1], a[2])",
            [generation, start, base64.b64encode(chunk).decode()],
        )
        time.sleep(0.02)
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

            page.on("response", _on_response)
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
            try:
                page.evaluate("() => window.anantaMachine.chat.open()")
                log("chat opened")
            except Exception as error:  # noqa: BLE001
                log("chat_open_err %r" % (error,))

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

                # Keep the synthetic avatar published so the tile is always visible.
                if avatar_generation is None or now - avatar_opened > 22:
                    try:
                        if avatar_generation is not None:
                            page.evaluate("(g) => window.anantaMachine.avatar.close(g)", avatar_generation)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        receipt = page.evaluate(
                            "(s) => window.anantaMachine.avatar.open(s, 'neutral-ai-v1')", avatar_source
                        )
                        avatar_generation = receipt["generation"]
                        avatar_opened = now
                        log("avatar open gen=%s" % avatar_generation)
                    except Exception as error:  # noqa: BLE001
                        log("avatar_open_err %r" % (error,))
                        avatar_generation = None
                if avatar_generation is not None:
                    try:
                        page.evaluate("(g) => window.anantaMachine.avatar.pulse(g)", avatar_generation)
                    except Exception as error:  # noqa: BLE001
                        log("avatar_pulse_err %r" % (error,))
                        avatar_generation = None

                for message in state.get("chat", []):
                    message_id = message.get("id", 0)
                    if message_id <= last_seen:
                        continue
                    last_seen = message_id
                    author = str(message.get("author", ""))
                    text = str(message.get("text", "") or "")
                    if not text or author in (DISPLAY_NAME, "System"):
                        continue
                    log("RECV %s: %s" % (author, text[:140]))
                    try:
                        reply = generate_reply(text)
                        log("REPLY %s" % reply[:140])
                        try:
                            page.evaluate(
                                "(a) => window.anantaMachine.chat.reply(a[0], a[1])", [str(message_id), reply]
                            )
                        except Exception as error:  # noqa: BLE001
                            log("chat_reply_err %r" % (error,))
                        pcm = synthesize_pcm(reply)
                        speak(page, speech_source, pcm)
                    except Exception as error:  # noqa: BLE001
                        log("reply_err %r" % (error,))

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