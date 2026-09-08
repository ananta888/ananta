# Versioned machine trust and signing-key selection

MAP-05 source audit: Ananta `740dfb7e5`, Meet `478856c`. The current Hub
signer has one private Ed25519 key; Meet has one public key/issuer and a
global capability ceiling. Independent project allowlists and key rotation
are missing. This change adds opt-in trust, not runtime approval.

## Contract before implementation

- Meet accepts `MACHINE_HUB_TRUST_PROFILE_JSON` (or its `_FILE` counterpart),
  mutually exclusive with the legacy public-key/issuer pair. Missing trust
  denies machines; malformed new trust fails startup, never falls back.
- Closed `ananta.meet-machine-trust.v1`: positive integer `revision`, exact
  HTTPS-origin `issuer`, nonempty subset `audiences` of existing v1/v2,
  `keys` (1..4), `scopes` (0..128, empty denies all). Unknown fields,
  duplicates, wildcards, private material and ambiguous configuration fail.
- Keys contain `kid`, canonical 32-byte base64url Ed25519 public `x`, integer
  Unix-second `notBefore`/`notAfter`. Distinct IDs and public keys required.
  The named key must cover current time and the entire grant lifetime.
  EdDSA only; no remote key lookup or fallback from an unknown key.
- A scope is one exact `{subject, tenantId, projectId, capabilities}` tuple,
  not a cross-product. Both this and the existing global ceiling apply.
  Signing never grants publisher consent, tools or broader Hub Task rights.
- Ananta optionally configures `ANANTA_MEET_MACHINE_KEY_ID` on its Hub
  signer, adding only protected JWT `kid`. Closed v1/v2 payloads, fresh
  authority checks and private-key isolation stay unchanged. No caller or
  Worker may select a key through request fields.
- Preinstalled overlapping key windows allow a planned Hub key switch
  without a Meet restart. Removing trust requires operator configuration
  replacement/restart, terminating this volatile server's memberships.
  No reload API is introduced. Replay state spans all installed keys.

Separate profile validation, JWT verification, HTTP and key loading
(SRP/DIP). The existing large server remains acknowledged SRP debt; only
composition wiring belongs there.

## Verification and remaining boundaries

Ephemeral real Ed25519/P-256 signatures, bounded HTTP/WS tests: exact scope,
unknown/early/expired/revoked keys, overlap, cross-key replay, malformed
configuration and unchanged legacy/human paths. Verify actual keyed Ananta
v1/v2 grants in Meet. Focused root tests and private-worktree Meet full check;
never replace live dist, secrets or deployment configuration.

Organization/Agent principals, operator provisioning APIs, public TURN and
production evidence remain separate subsequent work. MAP-05 is not complete
merely because the trust-profile slice passes.
