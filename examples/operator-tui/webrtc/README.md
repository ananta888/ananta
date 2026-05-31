# Operator TUI WebRTC Example

This example is intentionally local-first. It validates the Option C realtime
stack without requiring a live signaling service, STUN server, TURN server, or
Carbonyl binary.

## Mock ICE Probe

```bash
.venv/bin/python scripts/operator_tui_webrtc_smoke.py --mock --indent 0
```

Expected outcome:

```json
{"mode":"mock","summary":"mock: all probes succeeded"}
```

## Protocol And Policy Tests

```bash
.venv/bin/python -m pytest tests/client_surfaces/operator_tui/realtime -q
```

The tests cover:

- exact signaling allowlist semantics
- DataChannel protocol version and message type validation
- artifact size and SHA-256 integrity checks
- deny-by-default media policy
- WebRTC session id and session nonce enforcement

## Live Signaling

Live signaling must be configured with an explicit allowlist. A sibling host or
prefix match is not accepted; the scheme, host, port, and configured endpoint
path must match.
