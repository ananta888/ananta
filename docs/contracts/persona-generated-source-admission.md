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
