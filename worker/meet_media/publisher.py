"""One delegated publication in an ephemeral browser; no orchestration loop."""

import base64
import json
import time

from worker.meet_media.av_quality import MAX_VIDEO_BYTES
from worker.meet_media.browser_network import restrict_meet_browser_network
from worker.meet_media.publication_session import PublicationSession


def publish(meeting, text, video_path, deadline, lease):
    from playwright.sync_api import sync_playwright

    lease.require()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            chromium_sandbox=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        try:
            context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
            restrict_meet_browser_network(context, meeting["origin"])
            # No persisted human profile, certificates bypass, file input or device grant.
            context.add_init_script("""for (const name of ['getUserMedia', 'getDisplayMedia']) {
              navigator.mediaDevices[name] = () => Promise.reject(new Error('human_capture_forbidden'));
            }""")
            context.add_init_script("""(() => {
              const originalNow = Date.now.bind(Date);
              window.__realNow = originalNow;
              window.__clockOffset = 1000;
              Date.now = () => originalNow() + window.__clockOffset;
            })();""")
            context.add_init_script("""(() => {
              const orig = window.fetch; window.__diag = {};
              window.fetch = async (input, init) => {
                const response = await orig(input, init);
                try {
                  const serverDate = Date.parse(response.headers.get('date') || '');
                  if (Number.isFinite(serverDate)) window.__clockOffset = serverDate - window.__realNow() + 3000;
                  const url = typeof input === 'string' ? input : (input && input.url) || '';
                  if (url.indexOf('/config') >= 0) response.clone().json().then(v => { window.__diag.cfg = v; }).catch(() => {});
                  if (url.indexOf('/api/machine/sessions') >= 0 && (!init || (init.method || 'GET').toUpperCase() === 'POST')) {
                    response.clone().json().then(v => { window.__diag.session = v; }).catch(() => {});
                  }
                } catch (error) {}
                return response;
              };
            })();""")
            context.add_init_script("""(() => {
              try {
                if (!window.crypto || !window.crypto.subtle) { window.__diag.crypto = 'no-subtle'; }
                else {
                  crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveKey'])
                    .then(() => { window.__diag.crypto = 'ok'; })
                    .catch((error) => { window.__diag.crypto = 'genfail:' + String(error); });
                }
              } catch (error) { window.__diag.crypto = 'throw:' + String(error); }
            })();""")
            page = context.new_page()

            def _diag(text):
                with open("/tmp/meet-turn.err", "a") as handle:
                    handle.write(text + "\n")

            def _on_response(response):
                if "/api/machine/sessions" in response.url:
                    try:
                        _diag(f"NET {response.status} {response.url} {response.text()[:800]}")
                    except Exception as error:  # noqa: BLE001 - diagnostic only
                        _diag(f"NET {response.status} {response.url} <{error!r}>")
                elif "/api/machine" in response.url:
                    _diag(f"NET {response.status} {response.url}")

            page.on("response", _on_response)
            page.on("console", lambda message: _diag(f"CONSOLE {message.type} {message.text[:200]}"))
            page.on("pageerror", lambda error: _diag(f"PAGEERROR {str(error)[:200]}"))

            def _on_ws(ws):
                _diag(f"WS {ws.url}")
                ws.on("socketerror", lambda error: _diag(f"WSERR {error}"))
                ws.on("close", lambda: _diag("WSCLOSE"))

                def _on_frame(frame_type, payload):
                    try:
                        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
                        if "overlay" in text or "key-ack" in text or "membership" in text or "topology" in text:
                            _diag(f"WS{frame_type} {text[:400]}")
                    except Exception as error:  # noqa: BLE001 - diagnostic only
                        _diag(f"WS{frame_type} <{error!r}>")

                ws.on("framesent", lambda payload: _on_frame("SENT", payload))
                ws.on("framereceived", lambda payload: _on_frame("RECV", payload))

            page.on("websocket", _on_ws)
            page.set_default_timeout(min(5000, max(1, int((deadline - time.time()) * 1000))))
            url = meeting["origin"] + "/machine"
            session = PublicationSession(page, lease, url=url, deadline=deadline)
            lease.require()
            page.goto(url, wait_until="domcontentloaded")
            session.ready()
            with video_path.open("rb") as source:
                video = source.read(MAX_VIDEO_BYTES + 1)
            if not 16 <= len(video) <= MAX_VIDEO_BYTES or video[4:8] != b"ftyp":
                raise ValueError("meet_publication_media_invalid")
            page.evaluate(
                """() => {
                  const api = window.anantaMachine;
                  const origJoin = api.join;
                  api.join = (...args) => origJoin(...args).catch(error => {
                    window.__joinError = String((error && error.message) || error);
                    throw error;
                  });
                  const origPublish = api.publish;
                  api.publish = (...args) => origPublish(...args).catch(error => {
                    window.__publishError = String((error && error.message) || error);
                    throw error;
                  });
                }"""
            )
            try:
                session.call("join", [meeting["room_id"], meeting["grant"]])
            except Exception:
                _diag("JOINERROR " + str(page.evaluate("() => window.__joinError")))
                _diag(
                    "DIAG "
                    + json.dumps(
                        page.evaluate(
                            """() => ({ now: Date.now(), e2ee: window.__diag.cfg && window.__diag.cfg.mediaE2ee,
                              session: window.__diag.session })"""
                        )
                    )[:1200]
                )
                raise
            # Room membership changes on join can invalidate the E2EE overlay
            # (overlay_lifecycle_changed) mid-handshake. Wait for the membership /
            # route epoch to settle, then publish; retry once if it still races.
            published = False
            last_error = None
            for attempt in range(2):
                page.wait_for_timeout(8000 if attempt == 0 else 5000)
                try:
                    session.call("publish", [text, base64.b64encode(video).decode()])
                    published = True
                    break
                except Exception as error:  # noqa: BLE001 - retried with diagnostics
                    last_error = error
                    _diag("PUBLISHERROR#" + str(attempt) + " " + str(page.evaluate("() => window.__publishError")))
                    _diag("STATUS#" + str(attempt) + " " + json.dumps(page.evaluate("() => window.anantaMachine.status()"))[:600])
            if not published:
                _diag("CRYPTO " + str(page.evaluate("() => window.__diag.crypto")))
                raise last_error
            session.call("leave", [])
        finally:
            browser.close()
    return {"status": "published", "room_id": meeting["room_id"], "delivery_verified": False}
