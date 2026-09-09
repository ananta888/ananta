# Isolated peer for the long private dialog reference

The first pre-reserved two-hour attempt at Ananta `8b305d95a` / Meet `91d9203`
failed during fixture machine-page navigation in 13.29 seconds, before any long
dialog observation. Chromium reported repeated `ERR_NETWORK_CHANGED`. The
browser delivering the delegated source was already container-isolated, but
the fixture's receiving and bootstrap pages still ran on the host network.
This result remains failed; it is not a completed soak or proof that a particular
foreign process caused the notifications.

The test-only `MEET_ISOLATED_PEER_BROWSER=1` handshake lets the owning Python
fixture provision a second `DialogBrowserFixture` on the already owned internal
network. The companion requests only its own network/public certificate/SPKI;
the controller returns one closed owned-browser endpoint. Node connects once
within sixty-second handshake/fifteen-second connection bounds. Foreign-network,
noncanonical, duplicate/malformed replies, EOF and missing setup fail closed;
there is no host-browser fallback. Existing navigation retries and media/Hub
authority deadlines are unchanged. The ordinary companion fixture stays
compatible when this optional launcher is absent.

This reuses the existing nonroot, readonly, sandboxed 1-GiB/two-CPU/256-PID
browser container with no GPU, host ports, profiles or user-device mounts.
Partial setup is owned before launch and cleaned in the existing test finally
chain. Soak RSS/process observations include the additional container tree;
moving a process into a container must not hide it from resource accounting.
This separates the browser's network namespace, not the whole host's CPU/RAM
or hardware. It does not prove an Internet or independent physical host path.

The packaged browser speaks Playwright 1.58; the companion normally uses 1.62.
Only this test port therefore loads the existing Python installation's matching
Node client. The controller discovers that installed package itself, checks
name/version, hashes its bounded actual files and binds the path/version/digest
before execution, then verifies them again afterward. No application dependency
is downgraded, no new runtime package is downloaded, and no caller-provided
module is selected by the closed reference runner. The companion receives the
explicit dependency path and rejects missing/wrong-version clients, never
falling back to a host launch or different protocol.

`private-peer-smoke` is the closed short reference, followed by the original
`private-dialog-soak` profile. Both require explicit immutable browser/proxy
images and a clean source/private-build snapshot. The long profile retains its
7,200-second Task bound and separately reports active observation/cleanup time.
Initial deterministic checks passed: 82 Python cases in 46.79 s and 23 Node
handshake/navigation cases in 1.636 s. Actual new-port acceptance is next.

SRP/ISP: private infrastructure handshake, client dependency snapshot, native
container ownership and dialog assertions remain separate. The existing large
cross-repository fixture remains acknowledged composition debt; neither this
adapter nor the companion gains a Hub Task queue, identity issuer or Worker
orchestration authority. Test identities remain synthetic and production
release-ineligible.
# Native quality-failure diagnostics

The operation-stage repeat failed specifically at `consent-membership` in
32.66 seconds. The fixture began its 12-second peer UI wait immediately after
asynchronous dispatch, although real Worker navigation, client readiness and
join each have a separate existing 20-second limit. The fixture now observes
the actual join return before consent, with a 60-second startup bound and an
immediate failure wake-up. It neither fabricates membership nor changes any
Worker, Task, grant, consent or media-quality deadline. Startup phase and measured
join latency are content-free test properties. Short and long native verification
remain required; this ordering fix does not explain the earlier timing failure.

The ordered native repeat failed in 33.84 seconds at actual Worker startup
with `meet_dialog_session_expired`, before a join completed. This is not a fixed
startup incident or a successful gate. The observer additionally retains the
fixed failed startup phase and at most eight allowlisted HTTP/request/script
error observations per category, using existing events without extra browser
RPCs or raw exception/URL/token output.

The next repeat (35.20 seconds) localized failure to client readiness, with six
`ERR_BLOCKED_BY_CLIENT` request observations. The observer now classifies only
fixed certificate/timeout/connection/unknown fetch failures and queries the
private peer's bounded member/proxy-drop counts on startup failure. No network
policy, proxy connection limit or request timeout was enlarged. Eighty-four
startup/network tests passed in33.87 seconds; thirteen final observer checks
passed in12.09 seconds. Actual cause and native verification remain open.

The first isolated peer smoke reached actual chat and screen execution but
failed after 49.81 seconds with `meet_media_timing_source_failed`; it did not
establish soak readiness. The native fixture now retains at most one strictly
validated, content-free timing snapshot on rejection. Invalid snapshots produce
only a fixed marker. The real irreversible quality fence, its thresholds and
the original exception are unchanged. This observer is a separate test helper
(SRP); the existing large cross-repository fixture remains extraction debt.
