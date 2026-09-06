# Explicit Hub policy for persona images

`PersonaAssetPolicyService` is the concrete authorization adapter for the image
asset lifecycle. It composes project authority, `SqlPersonaImagePolicies`, the
Hub Evidence Registry's pinned-source verifier and an inspection-receipt port.
Workers receive execution assignments, not access to this policy service.

Policy installation and revocation require the project's `MANAGE` capability.
Only explicit named subjects can use a policy; project membership or tenant
administration alone does not imply permission to use/publish an image. Policies
specify a finite expiry and separate `inspect`, `store`, `preview` and `publish`
purposes. Worker/service execution credentials cannot install a user media policy.
Authenticated automation may invoke the same Hub operations without a UI or any
interactive approval. No tests or policy issuance require a human to continue.

Every policy pins **already registered** source identities and binding digests:
the image source, a separate `license_document`, and, when required, a separate
`media_consent`. All uploads and all personal likenesses require consent. Generated
images must retain a synthetic/test-only presentation classification. A registered
document proves its immutable admitted identity, **not** a license interpretation
or blanket grant. Installation is the explicit project-manager policy decision;
no policy is inferred just by uploading a document or finding an image online.

`HubEvidenceRegistryService.require_source_identity` looks up the exact scope and
identity, recomputes its immutable binding and compares the pinned digest. It
does not create identifiers or promote evidence. Unknown, revoked, mutated and
cross-project sources fail closed. Test/synthetic proof identities cannot back
a policy with a non-test-only asset classification. Each use repeats source,
expiry, subject, purpose, project access and current-policy-revision checks.

Policy content revisions are immutable. The current scoped head changes by
compare-and-swap; a revoked version records its actor. Revocation also consumes
a **tombstone revision**, which has no active policy payload. For example:
install revision 1 → revoke consumes revision 2 → an explicit regrant must use
expected revision 2 and create revision 3. An installation started against
revision 1 cannot undo that revocation. Old asset/admission snapshots remain
stale even if a later policy again grants the same purpose.

The image policy store, asset catalog and persona profiles use SQL `BIGINT`
revision columns to match their bounded 53-bit contracts across databases.
These new catalogs are not automatically activated in existing Hub deployments.

The policy adapter still requires the concrete Hub inspection task/receipt
adapter before asset upload can be activated. There is no permissive default
receipt verifier, no policy seed, no public upload activation and no live Meet
publisher grant in this change. Tests use actual Registry-issued **test** source
identities in isolated databases and explicit project-access fixtures.
