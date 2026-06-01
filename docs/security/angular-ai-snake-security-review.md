# Angular AI-Snake Security Review

## Scope
- OIDC PKCE flow (`OidcAuthService`)
- Session sharing transport (Hub Relay + WebRTC)
- E2E payload encryption (`E2eEncryptionService`)

## Findings
1. PKCE state/nonce validation is implemented.
- `state` from callback is matched against sessionStorage.
- `nonce` from ID token is validated before token adoption.

2. Token storage remains `localStorage`-backed.
- Risk: XSS can expose tokens.
- Mitigation: strict CSP, trusted templates only, no dynamic script injection, short token TTL.

3. E2E key exchange now uses ECDH P-256 + AES-256-GCM.
- Public key fingerprint is SHA-256 over SPKI.
- Fingerprint must be shown to users for manual trust confirmation when threat model requires MitM defense.

4. WebRTC policy gate enforces message-type allowlist and inbound rate limits.
- Unknown/disallowed messages are dropped.
- Violations are audit-logged.

5. Transport fallback behavior.
- Default order: WebRTC then Hub Relay fallback.
- Failure to establish P2P within timeout switches transport automatically.

## Residual Risks
- No hardware-backed key storage in browser.
- TURN credential lifecycle is environment-dependent; verify ephemeral credentials in production.
- Fingerprint confirmation UX is not mandatory by default and should be enforced in high-security environments.

## Recommended Hardening
1. Move access/refresh tokens to HttpOnly secure cookies where feasible.
2. Add strict CSP (`script-src 'self'`) and Trusted Types.
3. Enforce fingerprint confirmation for first peer contact.
4. Add explicit replay protection metadata for encrypted payload envelopes.
5. Add SOC/audit pipeline checks for `policy_violation` and repeated signaling failures.
