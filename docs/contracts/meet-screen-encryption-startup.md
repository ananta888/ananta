# Screen encryption startup investigation

## Observed failure, not yet a diagnosed cause

On 2026-09-08 the private root cross-repository text case passed, followed by
an image-avatar case whose initial screen had 349 received video packets but
zero decoded frames. The sender had delivered 33 source frames, E2EE was active,
ICE/PeerConnection connected, and no Hub/runtime rejection was recorded. An
isolated image-avatar repeat passed in 43.69 seconds. The source-profile feature
does not alter frame, codec, encryption or permission timing, and a green repeat
is not a transport fix.

Source inspection of companion `4d40b45` shows that `sframe.worker.ts` drops
frames while its encryption context has no key. It does not request a replacement
keyframe when that key later becomes available. Whether this explains the
observed receiver starvation is a hypothesis requiring a focused regression.

## Planned bounded diagnostic

Use the existing private two-browser Hub/Worker composition with synthetic keys
and synthetic inference. Delay only the sender SFrame Worker's first key message
by a fixed two seconds. Do not change key bytes, ACKs, encryption, receiver policy,
source/Hub expiry, timeouts or first-frame assertions. Keep the queue bounded and
wipe/cancel held test keys when that exact test Worker terminates. Observe only
counts/timing and numeric RTP keyframe/PLI statistics, never keys, packet bytes,
frames, SDP, ICE addresses or arbitrary errors. Exercise the injection helper
without browsers as well as the actual private transport case. No human capture,
public service mutation or production trust activation is involved.

If the hypothesis is reproduced, make the smallest separately reviewed native
keyframe-recovery change at the encryption transform, not in Hub authorization
or retrying the entire session. Otherwise retain the diagnostic and investigate
the measured counters rather than declaring a cause from an assumption.

## Implemented diagnostic and result (2026-09-08)

`tests/test_meet_screen_key_startup.py` now tests both sender and receiver key
delivery, using the existing private fixture with a separate test-only timing
adapter. Normal consent renewal can replace the first held key before delivery;
the injector therefore admits at most three incoming current generations until
one two-second delay completes, with one held key/timer, cancellation/wipe and
no protocol retries. Duplicate KIDs do not reset that timer or replay state.
An unexercised delay is a test failure, not a successful negative test. The
closed bridge exposes only counts and milliseconds; no production crypto changed.

The final serial run passed all three tests (JavaScript helper, actual sender
and actual receiver) in 55.55 seconds. Both real paths decoded moving screen,
completed two correlated synthetic chat replies, enforced the private-marker
source stop and obeyed Hub cancellation. Companion helper tests also passed.
The original intermittent zero-decoded-frame failure is **not reproduced or
fixed by these tests**. No speculative native keyframe-recovery change was made.
Keep the improved RTP/keyframe/PLI/transform-error counts for another occurrence.

Earlier attempts remain failures: missing bridge opt-in (7.89 s; fixture corrected),
an independent ERR_NETWORK_CHANGED before admission (12.50 s), and legitimately
cancelled injections (35.28, 17.01, 15.93 and 30.43 s). The first cancelled-injection
attempt initially surfaced as a later bridge timeout; the root now asserts its
closed observation immediately. Sender-only actual injection had previously
passed in 28.63 s, and the first genuinely exercised receiver repeat in 31.81 s.
None of these synthetic observations is GPU, public TURN or Registry-backed
production evidence.
