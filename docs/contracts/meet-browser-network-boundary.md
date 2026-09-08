# Fixed Meet browser HTTP and WebSocket boundary (MAP-10)

Source audit: Ananta `78323eaec`. Both ephemeral publisher runtimes navigate
to the Hub-assigned HTTPS origin and check the exact machine page before
handing over a grant. They block service workers, downloads and human media
capture, but do not restrict other HTTP or WebSocket destinations. The separate
offline status-screen context already denies network access; it is not the
machine participant's context and not an arbitrary agent-browser workspace.

Add one small execution-only adapter installed on the fresh browser context
before creating a page. Derive exactly one HTTPS and matching WSS origin from
the closed assignment; no caller-supplied allowlist or alternative origin.
Permit only same-origin GET/HEAD resources and the same-origin WebSocket;
reject credentials, noncanonical authorities, other schemes/ports/hosts and
other HTTP methods. Keep the original TLS verification and browser isolation.

Do not use a first-request URL check as a redirect guarantee. Playwright's
request continuation can follow redirects. Fetch an admitted resource with
`max_redirects=0`, `max_retries=0` and a finite timeout, then reject every 3xx
response before giving it to the browser. Never continue or retry after a
transport error. Dispose of fetched responses after use; do not retain bodies,
URLs, headers or exceptions in audit output.

Implementation investigation rejected Playwright WebSocket routing as a security
boundary: it installs a JavaScript mock, forwards messages through the routing
client and is not a worker-context transport restriction. A real private test
also hung inside `WebSocketRoute.close`; its original 45-second outer deadline
terminated it. Do not ship that interception. Append an additional enforcing
`connect-src` CSP allowing exactly the assigned HTTPS/WSS origins to every
admitted HTTP response, including dedicated-worker scripts. Preserve any
existing CSP as an independent intersecting policy. Native browser enforcement
also covers fetch, XHR, beacon and WebSocket connections from same-origin/blob
workers. Test actual worker sockets and redirects, not only document fetch.
No added native-socket wrapper or message forwarding in the final adapter.

Verify pure URL/method decisions, installation order and failure paths first.
Use real isolated local Chromium with private local test servers to prove that
same-origin assets and sockets work while direct and redirect attempts to a
second listener produce zero requests. Include subresources, popups and worker
requests; a response saying "blocked" without observing the destination is not
sufficient. Then rebuild the packaged Worker and run actual two-Worker dialog
integration against the already tested private Meet frontend.

This is a browser HTTP/WebSocket boundary, not a complete container firewall,
DNS-rebinding protection, WebRTC ICE/TURN destination policy, GPU exclusivity or
arbitrary agent-browser privacy implementation. Same-origin application code
remains trusted. Request fetching buffers responses in Playwright; existing
container memory and execution deadlines remain the outer resource bounds.
Do not mark all MAP-10/13/15 criteria done from this slice.

SRP/DIP: the adapter owns only the fixed transport restriction; existing
publication and dialog composition install it without acquiring Hub policy,
task scheduling or source-selection responsibilities. Existing broad dialog
composition debt remains; do not embed routing logic into that loop.

API references: [Route](https://playwright.dev/python/docs/api/class-route),
[BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext),
[CSP multiple policies](https://www.w3.org/TR/CSP3/#multiple-policies),
[connect-src](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/connect-src).
