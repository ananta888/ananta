# Speech/browser coexistence during avatar image changes

The private `avatar-image` cross-repository gate exposed incomplete PCM during
image replacement and the next spoken reply. Its failed runs remain failures;
remote picture decoding alone did not establish speech completion. Browser
source status became failed with only a partial sample count. Repeated source
and authority RPCs consumed the small producer budget. Merely removing one
duplicate status poll improved progress but did not complete both replies.

The corrected sink submits at most ten contiguous, individually valid 20-ms
PCM frames in one browser RPC. The queue remains 4,410 samples / 200 ms; the
worklet underrun rule, source deadlines, Hub freshness and revocation policy
are unchanged. Backpressure admits no partial batch. A browser error after
partial delivery closes that generation and never replays or claims completion.
The original single-frame sink remains available.

Responsibility boundaries are explicit (SRP/DIP): `DialogSpeechOutput` checks
current Hub control and spoken-reply bindings without browser I/O;
`SpeechPublication` validates contiguous PCM and exact queue accounting;
`BrowserSpeechPort` checks live joined state, the exact Meet lease and open chat
before and after each source operation in the same RPC. The actual browser
source still checks its own generation/authority for each frame. There is no
separate 50-ms browser-check cache. Cleanup needs no new publication authority.

The same work identified a separate deterministic race: a concurrent passive
avatar selection could make `MeetAuthorizationClient` reject the whole parent
membership. Its post-I/O comparison now treats already-negotiated source
selection like independently checked controls. Negotiation presence, owner,
runtime, deadline, capabilities and every membership identity remain fenced.
The focused regression first failed for image selection, then all seven cases
passed in 11.27 seconds. The image hydrator still rechecks its exact selection
before and after content access.

The corrected real Hub/Worker/Meet gate passed in 45.66 seconds: two exact local
completions of 220,500 samples each, red-to-blue image replacement during the
first reply, image revocation in 122.79 ms locally / 180.19 ms remotely, and a
second correlated, non-silent remote reply after revocation. Screen continued,
two avatar generations were observed, and capture/transform errors were zero.
Remote counters are cumulative transport observations, not exact delivered PCM.
Images, audio and admission policy are synthetic; this is not GPU, production
release or Hub-registered SRC/RUN evidence. The final targeted sink, browser,
source-control and race regression passed: 98 tests in 93.49 seconds, including
actual JavaScript execution of the bound pre/post-access checks.

The existing synchronous top-level control exchange remains a timing/coupling
limitation under slow backchannels. This change does not claim immunity to
arbitrary network stalls: unchanged watchdogs must stop stale sources. A future
asynchronous exchange adapter must preserve request-age/freshness fencing and
must not create another task orchestration loop.
