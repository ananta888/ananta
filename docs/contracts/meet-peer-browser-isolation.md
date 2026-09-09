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
