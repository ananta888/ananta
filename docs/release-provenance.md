# Release Provenance And Attestation

Ananta currently publishes source tags plus release evidence assets. The release workflow prepares checksums and an SBOM-like component inventory for each GitHub Release.

## Current Decision

For the current release-preparation phase:

- GitHub release assets are protected by `SHA256SUMS`.
- `release-verification-report.json` records release-gate results.
- `release-sbom.json` records Python and npm dependency components from lockfiles.
- The Git tag remains the source-of-truth provenance anchor.
- Container signing or artifact attestation is not enabled until official container publication is adopted.

This avoids pretending that unsigned local build outputs are fully attested release artifacts.

## Future Attestation Path

When official release containers or publishable binary artifacts are added:

1. Use protected `release` GitHub Environment credentials.
2. Prefer keyless GitHub OIDC provenance where possible.
3. Attach image digest, SBOM and release verification report to the release evidence.
4. Keep signing or attestation steps in the release workflow, not in ad hoc local commands.
5. Document verification commands for consumers in `docs/release-process.md`.

## Review Requirements

Any PR that enables signing, attestation or package publishing must:

- update this document
- update `docs/github-secrets-inventory.md`
- use minimal workflow `permissions`
- include a rollback plan
- keep generated attestations tied to immutable tags or digests
