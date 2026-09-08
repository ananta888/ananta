"""Observe real private destinations, not just mocked route decisions."""

import os
from unittest.mock import patch

from tests.meet_browser_network_server import serve
from worker.meet_media.browser_network import restrict_meet_browser_network

SOCKET = """url => new Promise(resolve => {
  const socket = new WebSocket(url);
  let settled = false;
  const done = value => {
    if (settled) return;
    settled = true;
    clearTimeout(timer); resolve(value);
    try { socket.close(); } catch { /* Closing a failed handshake cannot hang the test. */ }
  };
  const timer = setTimeout(() => done('timeout'), 2500);
  socket.onmessage = event => done(event.data);
  socket.onerror = () => done('denied');
  socket.onclose = () => done('denied');
})"""

WORKER = """({url, kind, source = null}) => new Promise(resolve => {
  const script = `onmessage = async e => {
    const {url, kind} = e.data;
    try {
      if (kind === 'socket') {
        const socket = new WebSocket(url);
        socket.onopen = () => { socket.close(); postMessage('unexpected'); };
        socket.onerror = () => postMessage('denied');
      } else if (kind === 'script') {
        importScripts(url); postMessage('unexpected');
      } else {
        await fetch(url); postMessage('unexpected');
      }
    } catch { postMessage('denied'); }
  };`;
  const blob = source || URL.createObjectURL(new Blob([script], {type: 'text/javascript'}));
  const worker = new Worker(blob);
  const done = value => { worker.terminate(); if (!source) URL.revokeObjectURL(blob); resolve(value); };
  const timer = setTimeout(() => done('timeout'), 2500);
  worker.onmessage = e => { clearTimeout(timer); done(e.data); };
  worker.onerror = () => { clearTimeout(timer); done('worker-failed'); };
  worker.postMessage({url, kind});
})"""


def run(pem, private, spki):
    from playwright.sync_api import sync_playwright

    with (
        patch.dict(os.environ, {"NODE_EXTRA_CA_CERTS": str(pem)}),
        serve(pem, private) as (foreign, denied, _),
        serve(pem, private) as (origin, seen, redirects),
        sync_playwright() as playwright,
    ):
        redirects.update(
            {
                "/redirect": foreign + "/redirect-sink",
                "/same-redirect": origin + "/same-sink",
                "/socket-redirect": foreign.replace("https:", "wss:") + "/socket",
            }
        )
        browser = playwright.chromium.launch(
            headless=True, chromium_sandbox=True, args=["--ignore-certificate-errors-spki-list=" + spki]
        )
        print("fixture-phase:browser", flush=True)
        try:
            context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
            restrict_meet_browser_network(context, origin)
            page = context.new_page()
            page.set_default_timeout(5000)
            page.goto(origin + "/machine", wait_until="domcontentloaded")
            print("fixture-phase:page", flush=True)
            assert page.evaluate("fetch('/asset').then(r => r.text())") == "synthetic-asset"
            assert page.evaluate("typeof window.__pwWebSocketDispatch") == "undefined"
            assert page.evaluate(SOCKET, origin.replace("https:", "wss:") + "/socket") == "synthetic-ready"
            print("fixture-phase:socket", flush=True)
            assert page.evaluate(SOCKET, foreign.replace("https:", "wss:") + "/socket") == "denied"
            print("fixture-phase:foreign-socket", flush=True)
            assert page.evaluate(SOCKET, origin.replace("https:", "wss:") + "/socket-redirect") == "denied"
            print("fixture-phase:socket-denials", flush=True)
            for target in (foreign + "/fetch-sink", origin + "/redirect", origin + "/same-redirect"):
                assert page.evaluate("url => fetch(url).then(() => 'unexpected', () => 'denied')", target) == "denied"
            assert (
                page.evaluate("fetch('/post-sink', {method: 'POST'}).then(() => 'unexpected', () => 'denied')")
                == "denied"
            )
            assert (
                page.evaluate(
                    """url => new Promise(resolve => {
              const img = new Image(); img.onload = () => resolve('unexpected');
              img.onerror = () => resolve('denied'); img.src = url;
            })""",
                    foreign + "/image-sink",
                )
                == "denied"
            )
            with context.expect_page() as popup:
                page.evaluate("url => { window.open(url); }", foreign + "/popup-sink")
            popup.value.wait_for_load_state("domcontentloaded")
            popup.value.close()
            print("fixture-phase:http-denials", flush=True)
            for kind, target in (
                ("fetch", foreign + "/worker-sink"),
                ("socket", foreign.replace("https:", "wss:") + "/socket"),
                ("script", foreign + "/worker-script-sink"),
                ("fetch", origin + "/redirect"),
            ):
                assert page.evaluate(WORKER, {"url": target, "kind": kind}) == "denied"
            for target, expected in ((origin, "allowed"), (foreign, "denied")):
                assert (
                    page.evaluate(
                        WORKER,
                        {
                            "url": target.replace("https:", "wss:") + "/socket",
                            "kind": "socket",
                            "source": origin + "/worker.js",
                        },
                    )
                    == expected
                )
            page.goto(origin + "/strict", wait_until="domcontentloaded")
            assert page.evaluate("fetch('/strict-sink').then(() => 'unexpected', () => 'denied')") == "denied"
            assert page.evaluate(SOCKET, origin.replace("https:", "wss:") + "/socket") == "denied"
            assert "/asset" in seen and "/socket" in seen and "/redirect" in seen
            assert "/same-sink" not in seen
            assert "/post-sink" not in seen and "/strict-sink" not in seen and seen.count("/socket") == 2
            assert denied == [], "foreign destination was contacted despite the browser boundary"
            print("fixture-phase:worker-denials", flush=True)
            return {"foreign_requests": len(denied), "http_asset": True, "websocket_greeting": True}
        finally:
            browser.close()
