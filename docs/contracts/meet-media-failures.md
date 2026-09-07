# Content-free media capability failures

The bounded media subprocess now has a closed capability-error type and five
reserved local exit codes. Only that type maps to a specific exit; ordinary
exceptions, even with matching text, remain the existing generic failure.
No model text, filenames, FFmpeg stderr or provider exception crosses this
boundary. Child process supervision, task deadlines and replay checks remain.

| Local exit | Public machine-readable code |
| --- | --- |
| 80 | `meet_video_encoder_unavailable_or_failed` |
| 81 | `meet_video_encoder_timeout` |
| 82 | `meet_piper_cuda_unavailable` |
| 83 | `meet_piper_cuda_fallback_forbidden` |
| 84 | `meet_piper_cuda_budget_unavailable` |

For an authenticated turn, the Worker returns HTTP 503 with the closed
`ananta.meet-media-failure.v1` envelope: schema, task ID, dispatch lease ID and
`error: {code}`. A separate HMAC domain binds both the exact request-byte hash
and response bytes. The Hub accepts the code only after checking that signature,
the 1-KiB body budget, exact task/lease, closed fields and the code allowlist.
Duplicate JSON keys, replay onto different request bytes, unknown fields/codes,
wrong status/signature and oversized responses retain `meet_worker_unavailable`.
Error response streams are closed on both valid and invalid replies.

Existing success envelopes/signatures are unchanged. Old Hubs still see a
generic HTTP failure; new Hubs treat unsigned/legacy Worker errors generically.
Dialog admission does not reuse this turn-specific error authority. There is
no automatic retry, human approval, CPU codec, cloud voice or permission change.
The existing conservative capacity quarantine on execution errors remains.
Specific failure codes are operational diagnostics, not hardware attestation,
Registry evidence or a production release grant.

Structure: the dependency-light closed protocol owns the whitelist and
signature; Worker modules report typed failures; a small Hub adapter verifies
the HTTP boundary (SRP/ISP/DIP). Existing Hub imports from the older Worker
contract/HTTP helper are preserved compatibility debt, not a new orchestration
path. The large older HTTP adapter is not expanded with raw error parsing.

On 2026-09-07, 122 media, speech, encoder, HTTP-limit and dialog-transport tests
passed in 85.49 seconds. The expanded failure suite passed 35 tests in 31.17
seconds, including the actual runtime entrypoint, real supervised child exits
and signed HTTP connections. Both current-source RTX clip/speech gates passed
again in 15.79 seconds. All fixtures are headless and synthetic; no serving
container, public service or trust configuration was changed.
