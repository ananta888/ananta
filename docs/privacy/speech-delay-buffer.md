# Short-lived source-audio correction buffer

Delayed correction uses a browser-local ring buffer separate from the existing
long-run spool and every evidence/training store. It has no dataset address or
export API. A non-extractable per-instance AES-256-GCM key encrypts each segment
with session, epoch, segment, source digest and expiry as authenticated data.
Before encryption the browser verifies that SHA-256 of the supplied bytes is
the declared source digest. A mismatch is rejected without creating a key or
buffer entry.

The service retains at most five encrypted segments, 24 MiB total and ten
minutes. Confirmation, correction completion, revoke, expiry, session end,
quota eviction, key loss, stream 404, stop 409 and an offending 413 remove
ciphertext and metadata idempotently. It owns no recurring timer; expiry is
checked on every operation. A renderer crash loses the in-memory key and map,
so it cannot leave recoverable plaintext on disk. Callers receive a temporary
decrypted copy only inside `SpeechDelayBufferService.use`; the service erases
that copy in a `finally` block after the bounded correction attempt. The public
surface deliberately has no list, export, dataset, training or persistent
storage operation.

When per-segment correction is enabled, the product coordinator decrypts one
entry only for a single bounded request to the Hub's
`POST /v1/voice/source-corrections` endpoint. The Hub binds the request to the
current strict-E2EE session epoch, security-contract digest, source digest and
exact bilateral consent version/revocation epoch. It rechecks session
membership and consent both before delegation and at completion fences, so a
mid-request revoke prevents correction publication and idempotency completion.
Source ASR is represented by a Hub-owned child task; the route does not retain
the uploaded bytes, and correction delegates to the canonical
`voice_runtime.fusion.alignment` path rather than implementing another
alignment algorithm.

Live transcript display and segment rotation are independent settings. A final
revision remains visible if source audio is missing, consent is absent, or
correction fails; the revision store exposes the bounded failure reason instead
of hiding the final. Pause and ordinary-audio override stop semantic ingestion
and purge the short-lived correction buffer. Re-enabling correction never
retries an already attempted final revision.
