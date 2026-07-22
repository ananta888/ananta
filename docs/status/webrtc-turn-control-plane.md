# WebRTC TURN control-plane status

The repository contains hub-owned policy seams for short-lived credentials,
pinned destinations, payload-blind accounting, quota reservations and
receiver-specific degradation. These seams do not make TURN production-ready.

Current release decision: `no_go`.

The following durable or runtime integrations are intentionally absent:

- `network_profiles.py` is not wired to the credential and endpoint services.
- Credential state, accounting cursors, receipts, quota reservations and
  receiver state use bounded in-memory reference ports only.
- Signing keys have no external secret-manager adapter.
- No coturn authorization hook, allocation revocation adapter, persistent
  accounting repository, collector identity or shared multi-hub CAS exists.
- No real DNS, certificate, coturn, browser, network, restart or chaos evidence
  with valid `SRC_*` or `RUN_*` identifiers has been produced.

Until these gaps are closed, the empty endpoint catalog remains fail-closed and
the new services must not replace production admission or expose credentials.
