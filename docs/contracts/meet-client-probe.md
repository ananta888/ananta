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
