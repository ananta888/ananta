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
