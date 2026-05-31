# Operator TUI Window Bridge Security

## Scope

This document defines the trust boundary for the local external-window bridge used by `ananta tui`.

## Trust Boundary

- Bridge server binds to loopback only (`127.0.0.1`).
- Only local clients are accepted.
- Requests require `X-Ananta-Window-Token` session token.
- External window is a render/input surface only; TUI remains control plane.

## Controls

- `localhost`-only bind and local-client enforcement.
- Per-session token auth for `/state` and `/action`.
- Allowlist-only action protocol (`windowing/protocol.py`).
- Duplicate event protection (`event_id` replay rejection).
- Inbound rate limit (30 actions/second).
- Bounded queues to avoid unbounded memory growth.

## Reason Codes

- `window_bridge_non_local_client`
- `window_bridge_unauthorized`
- `window_bridge_action_not_allowed`
- `window_bridge_duplicate_event`
- `window_bridge_rate_limited`

## Residual Risks

- Local same-user processes can still attempt requests; token leakage remains a local compromise risk.
- Browser engine/runtime vulnerabilities are out of scope of bridge protocol hardening.

## Operational Guidance

- Restart bridge via `:center.window.restart` if window gets desynced.
- Use `:center.window.status` for diagnostics (`dropped/rejected/accepted` counters).
