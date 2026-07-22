# SFU broadcast child supply-chain gate

The child gate validates the delta introduced after the parent semantic-media
supply-chain gate. It neither synthesizes an SBOM nor treats a local package
listing as deployed-image evidence.

The external producer must provide:

- a CycloneDX 1.6 or SPDX 2.3 inventory for the actually deployed hub,
  frontend, SFU, TURN, browser adapter and LiveKit SDK;
- vulnerability, malware, secret, license and provenance results bound to the
  same source, lockfile, policy, infrastructure and image digests;
- runtime container-control observations for non-root, read-only rootfs,
  capabilities, sandboxing, network policy, secret references, health checks
  and resource limits;
- a non-empty, digest-bound delta from a fresh parent SBOM.

An exception requires bounded scope, owner, rationale, expiry, compensating
control and externally verified approval binding. Expired or unverified
exceptions, unknown licenses, floating references, unresolved critical/high
findings, missing provenance, or control-plane logic in SFU/TURN fail the gate.
Reports contain counts and digests only.

Missing scanner tools, images, parent evidence or runtime controls are failed,
never skipped. A technically passing report still cannot activate broadcast
while parent readiness is no_go or observe_only.
