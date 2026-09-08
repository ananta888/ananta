# Installed Meet client feasibility (MAP-08)

Source audit `3676a9f70` / Meet `9de2fa4`: the Worker currently checks browser
method presence; Meet's legacy `capabilities()` describes its initial MP4
contract, not the later dialog ports. Companion plan:
`docs/machine-client-probe.md`, tracked by MDS-01.

Add a strict pure validator for the separate closed
`ananta.meet-client-probe.v1` projection. Its fixed isolated-browser profile
reports secure context, encoded-transform support, VP8/Opus send/receive and
installed port versions. Check only the media and port requirements of the
Hub's closed assignment. A present invalid, unknown or unsupported result
fails before join; no native/LiveKit/plaintext retry or Worker profile selection.
Absence retains only the existing legacy fixed-browser path, not a verified
probe or relaxed SFrame requirement. The legacy MP4 API and assignment v1 wire
stay unchanged. Probe results are local technical feasibility, not grant,
attestation, negotiated ACK, delivered frames or production evidence.

Use a small reusable browser probe reader inside existing deadline/navigation/
lease checkpoints. Keep pure validation out of session orchestration (SRP/DIP).
Neither helper may call capture, model, network or policy ports. Preserve the
existing broad runtime composition debt, do not add a second scheduler.

Verify exact types/keys/versions, missing and malformed distinctions, required
versus irrelevant ports/codecs, no grant handoff after failure, post-read
navigation/expiry fencing, legacy compatibility and actual Chromium/Firefox
projections. Follow with a private Hub/Worker dialog and isolated Meet full check;
the separately tracked intermittent decoder-start problem is not fixed by this
probe. No serving runtime, trust, credentials or human capture is activated.

## Implemented consumer

`ananta_contracts.meet_client_probe` validates exact fields, versions and actual
booleans before checking assigned ports/codecs. All 63 nonempty capability sets
are exercised. Each direction remains independent: audio receive does not need
video, MP4 needs its legacy publication port and both sender codecs, and absent
unused dialog ports do not invalidate a compatible MP4 client. Secure context
and encoded-transform support remain mandatory, including a chat-only machine.

`worker.meet_media.client_probe` distinguishes genuinely absent from malformed,
throwing and Promise-returning probes. It never waits for an arbitrary probe
Promise or invokes join, capture or source methods. Both session adapters keep
their original deadlines and check navigation/current authority before and after
the read; failed dialog readiness closes the session before any grant handoff.
The dialog runtime supplies only the existing assignment capability set. Legacy
absence is explicitly `False`, not verified readiness or a native fallback.

The first 105 consumer/session checks passed in 72.95 s. The expanded regression
then passed **330 tests in 218.00 s**, including every source-profile/media/
persona/capacity/child-fence case from the previously cleanup-failing set, real
Hub/Meet principal interoperability, additional probe requirements/fencing and
publisher/runtime cleanup. No teardown errors remain. Ruff and the 81-file
standalone Worker boundary scan passed. Actual current Meet browser and installed
Worker integration remain to be checked before the probe slice is complete.

## Real browser and packaged integration

Meet `025d9ae` passed its full isolated check: 676 frontend tests and 762 Node
tests, zero failures, two explicit Node skips; build/static/security/Go unit/vet
passed. Both actual Chromium and Firefox machine pages expose the exact frozen
projection without probe-triggered capture, device enumeration, HTTP, keys,
PeerConnection, WebSocket or join. Missing transforms return unsupported, and
the old `capabilities()` object remains byte-shape compatible.

The complete Worker image built from `89956ecaf`, using the already verified
dependency layers, is
`sha256:01db5060da48dd832588c84e00e2454b6792020a8236657ed1936f7df33de984`.
The build client exited successfully before testing. Both real Hub/two packaged
Worker cases passed together in **105.70 s**, without application source mounts:
independent screens, two admitted persona images, overlapping speech and
independent revocation/stop against the fresh Meet build. Thus the additive probe
and strict installed Worker consumer work together under actual machine grants.

A further selected-voice GPU dialog generated both answers but failed later
during the second output because its Hub control state became stale. See
[control freshness diagnosis](meet-control-refresh-latency.md). It is neither a
failed capability probe nor a completed GPU-dialog acceptance; the mandatory
stop was preserved. MAP-08/11/24 remain open for their outstanding runtime
criteria. No production, public TURN or native-interoperability claim follows.
