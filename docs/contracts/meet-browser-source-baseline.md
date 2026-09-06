# Browser live-source baseline

The existing `BrowserTaskContract` now accepts an optional closed `live_view`
request (`ananta.browser-live-request.v1`). Absence/null preserves ordinary
browser behavior. This is bounded intent, **not** a sharing grant or an owned
workspace/assignment identity.

Limits: 320–1280 by 180–720 even pixels, 1–10 FPS, at most 30 seconds, 64 KiB–1 MiB
per encoded frame and exactly one pending frame. Defaults are 640×360, 5 FPS,
30 seconds and 256 KiB. Source kind is only `agent_browser`; sensitive-content
policy is only `deny_unknown`. Unknown fields, string/boolean numeric budgets,
host desktop selection, paths, URLs and caller-created grants are rejected.
Downloads and session persistence are incompatible. The live duration cannot
exceed the parent task timeout. Requesting authenticated content additionally
requires the existing task's explicit auth opt-in; it still does not grant media
publication permission.

## Existing adapter limits

`BrowserCamofoxAdapter` currently provides action/page/screenshot REST operations,
not a Hub-lease-bound continuous live source. Its session creation rejects live
requests before network activity. `BrowserUseExecutionAdapter` similarly returns
`blocked/browser_live_source_unsupported` before actions; command availability
or a synthetic default runner cannot attest a live stream. Existing ordinary
actions remain compatible.

The legacy browser services mix concrete HTTP calls, policy checks and audit
operations (preserved DIP/SRP debt); the existing task parser also retains its
legacy permissive coercions for old fields. The new media contract is separate,
immutable and strict, and does not inherit those coercions as capture authority.
No existing browser session ID, personal profile or arbitrary remote page is
admitted as a media source by this change.

## Actual isolated Chromium probe

`worker.meet_media.browser_source_probe` runs only in a non-root headless
environment. In the dedicated private media container it launches an ephemeral
Chromium context with sandbox enabled, no permissions/downloads/service workers,
and blocked page network requests. It opens one probe-owned `about:blank` page
whose trusted DOM alternates red/green. CDP `Page.startScreencast` continuously
delivers JPEG frames; the probe decodes their dimensions and actual center pixels,
acknowledges frames, retains counters/colors only, and closes the browser.
It does not call a screenshot endpoint or a human capture picker.

Observed on the local worker image
`sha256:6de07d65065e863d544494f4b0c3c886c4c4fb7815c12c42a0b133251e241d6e`:
Chromium 145.0.7632.6, sandbox enabled, 17 decoded 640×360 frames in 2012 ms,
both expected colors, 28,331 encoded bytes, 2514 ms total, 601 ms child CPU and
117,068 KiB peak child RSS. Peak child RSS is not aggregate container/browser
memory. These simple color frames are not a representative website bandwidth
benchmark. No page network request or human capture was used.

Run the opt-in container gate with `MEET_BROWSER_PROBE_GATE=1` and
`tests/test_meet_browser_probe.py`. The driver enforces a 30-second process limit
and a 45-second test limit. It needs the provisioned private browser image; it
does not attach to the desktop or another browser profile. Unit tests need no
browser or person.

## Not yet delivered

This is a synthetic technical capability/cost observation, not Registry-backed
production release evidence and not decoded Meet reception. Hub-owned workspace
admission, exact page/navigation epochs, privacy fencing/secret-marker receiver
tests, publication controls and the MDS-05 source transport still need integration.
No `live_view` request can currently activate that unimplemented production path.
The separate Meet MDS companion still lists MDS-01 through MDS-09 as todo in the
locally inspected repository; ongoing Broadcast changes are not a substitute for
those dialog/receive/source/renewal endpoints.
