# Image and spoken replies across three real lease renewals

## Source check (MAP-11/20/29/30)

The existing `avatar-image-renewal` scenario verifies one actual lease change.
The earlier five-minute text/screen soak observed multiple generations but its
speech branch pauses speech before the long observation. Neither is evidence
of successful new spoken replies and image hydration through three renewals.

Add a separate opt-in `avatar-image-renewal-series` case, selected only with
`MEET_DIALOG_SOAK_SECONDS=360`. Keep real clocks, the production 120-second
grants and normal Hub renewal. Use a 360-second parent and the existing isolated
fixture lifetime; no accelerated expiry, substitute lease or policy retry.

After red/blue replacement and the first complete spoken reply, require actual
generations 2, 3 and 4 in order. Each must have matching image hydration, a
decoded blue remote avatar, moving screen, no old speech replay, and a newly
correlated spoken reply completed locally plus non-silent remote audio. Then
revoke the image and verify that another spoken reply still works before final
parent stop. Retain the existing single-renewal scenario unchanged.

Keep the observer passive and bounded; extend its exact-generation wait instead
of embedding renewal policy in a test Worker. No generated identities, grant
replays, human capture, production credentials or unrelated process changes.
Tests classify image, policy and tone as synthetic. This is a private single-host
multi-renewal check, not a six-minute active soak, two-hour soak, real GPU run,
multi-agent/multi-host acceptance or production release evidence.

Exercise the generation selection and scenario classification with deterministic
fixtures first, then run the one extended browser case serially. Preserve any
failed attempts and fix their actual cause without extending safety budgets.

The first series attempt failed in 202.34 seconds while checking the blue
avatar after a renewal. Only the remote screen remained, and the Worker reported
`meet_dialog_session_changed`. The original guard combines membership, lease,
room and control-revision mismatches, so this is not yet a causal diagnosis.
Split its unchanged comparison into a pure, content-free reason classifier and
snapshot existing callback/renewal observations in the failing assertion. Never
log raw session IDs, room IDs, grants or contents. A failed multi-renewal gate
is not a completed acceptance criterion.

The diagnostic repeat failed in 202.26 seconds at generation 4. The assertion
snapshot contained **no runtime error**; authenticated callbacks and matching
image hydration had reached generations 1–4. The old mutable exception list
had therefore conflated teardown with the original missing-avatar observation.
Retain the pure session mismatch classifier, and additionally observe bounded,
content-free local avatar phase transitions and existing pump failure codes.
Those observations must not change control policy, source expiry or cleanup.

The third diagnostic failed in 201.42 seconds, with local avatar generation 8
still open and no pump or runtime errors. Source/build inspection then found a
concrete mismatch: Meet's existing `dist/browser/index.html` predates its sender
reuse correction in `26d1076`; the built JavaScript contains no
`sender_slot_limit`, while the current source does. These runs used the old
browser build, not a verified build of the reported source revision. They
therefore cannot establish a regression in the current transport source.

Build current Angular sources into a new private temporary directory and pass
it through the fixture's explicit `MEET_TEST_PUBLIC_DIR` option. Do not rebuild
or replace serving `dist`, restart a deployment, or change any trust. Add a
conservative source/build freshness preflight to the Ananta cross-repository
gate before it creates browser/network resources. A timestamp check is only an
early stale-build diagnostic, never cryptographic build or release evidence.

Current isolated Angular build (`/tmp/ananta-meet-build-y78nEH`, not serving
`dist`) passed all three renewals, matching remote blue avatar, moving screen
and four complete local 220500-sample replies. The overall run still failed in
235.94 s: the fifth reply after image revocation closed prematurely. A further
diagnostic run failed earlier in 50.21 s and recorded the exact worklet error
`meet_speech_worklet_underrun`, with a 287.9-ms speech RPC and a 297-ms tick.
No runtime denial preceded that failure. The timestamp preflight passed for the
private build and correctly rejected the stale default build; 45 focused tests
passed in 25.98 s. Record per-publication tick gaps rather than one accumulated
maximum, and retain only eight allowlisted worklet error codes, never PCM.

The remaining failure is the cross-process PCM producer cadence, not a missing
human approval or a reason to enlarge the 200-ms worklet queue. The next bounded
implementation slice is described in [browser PCM feeding](meet-browser-pcm-feeding.md).
