# Generated persona outputs: explicit Hub admission

## Source audit and implementation plan (2026-09-09)

At `49b493f80`, image/video assets already have delegated bounded decoding,
immutable artifact storage, pinned source/license/consent policy, scoped queries,
revocation and erasure. Voice presets and profile selection also exist; the
older MAP-18 note saying video/voice integration is absent is stale. However,
the `generated` origin label does not itself establish a generation run.

Add an independent generated-output admission service and closed manifest.
It accepts image/video bytes only under a successful, pre-reserved Hub run
whose exact result digest binds media type, size/hash, owner, source inputs,
separate license/consent pins and intended classification. Validate current
project MANAGE authority before reading a run, before source issuance and
before returning its pin. Workers/services cannot use the management API.
Unknown/stale/mutated runs, mismatched bytes, scopes or owners fail closed.

Only the Hub Registry issues the new source identity; the origin digest binds
the verified run and result. The service does not mint a run after execution,
install publication policy, infer likeness consent, fetch remote URLs or
decode inside the Hub. Its source pin feeds the existing policy/inspection/
artifact lifecycle; admission is not permission to publish. Synthetic *test
evidence* remains test-only. A real generated asset may be labelled synthetic
content with local non-test evidence; these are distinct classifications.

Keep schema/result hashing, authority verification and HTTP/bootstrap adapters
separate (SRP/DIP), reusing Registry and project access ports. Do not add policy
or receipt branches to the generic artifact store or Worker publisher.
Tests must cover actual SQL Registry reservation/completion, all binding and
scope mutations, authority revocation between stages, no side effects on
failure, exact idempotent source issuance, closed headless HTTP and continued
image/video policy and inspection compatibility.

This first adapter admits outputs of an already verified generation task; it
does not claim to implement a generator or complete MAP-18. The next step must
connect a real Hub-delegated local generator to this receipt contract and the
existing inspection/artifact chain, then verify that complete path. No live
project, public admission policy or production evidence is changed by tests.

## Implemented receipt ingress

`ANANTA_PERSONA_GENERATED_SOURCES_ENABLED=1` explicitly enables the Hub-only
`POST /api/persona-media/v1/projects/<project>/generated-sources` adapter.
The exact JSON fields are `output`, `run_pin` and base64 `content`; the closed
models are in `agent/models/persona_generated_source.py`. Image input is bounded
to 5 MiB and video to 3,500,000 bytes. This stage hashes bytes but never decodes,
persists raw media, generates content, installs policy or authorizes publication.
The response contains only the Registry source pin and
`publication_authorized: false`. Existing inspection remains mandatory before
an asset is stored or selected. A final concurrent revocation may leave an
immutable source fact, but the request fails without returning a stale pin.

The initial 45 SQL/negative checks passed in 25.33 seconds; 97 HTTP/bootstrap/
legacy checks passed in 43.19 seconds. The final expanded receipt/policy and
image/video artifact-chain selection passed **103 tests in 45.20 seconds**.
Those chain tests use actual Hub inspection Tasks, separate pre-reserved
inspection runs, SQL catalog and immutable files, with explicitly synthetic
generation/decoder doubles. Preview does not imply publication and policy
revocation denies further reads. The first chain attempt failed twice because
its private database lacked Artifact/Version tables; adding the normal schema
to that test fixture fixed setup without changing product checks.

This is not yet a real generator execution. MAP-18 remains open for the native
generator-to-admission chain. Existing generic publisher/route composition is
preserved; the new model, admission service, route and bootstrap each have one
responsibility and injected Registry/authority seams (SRP/DIP/ISP).
