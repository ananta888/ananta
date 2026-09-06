# Hub-pinned local speech profile

`ananta.meet-speech-profile.v1` binds the existing German Piper preset, its
upstream revision, model/configuration SHA-256 digests, mono PCM16LE/22,050-Hz
format and a strict 1–40-second audio budget. It is an execution configuration,
not a `SRC_*`/`RUN_*` identity, personal voice consent or publication grant.
The sole supported voice remains `piper.de_DE.thorsten.medium`. Arbitrary
voices, languages, model URLs, config paths and caller-created hashes fail
closed. Voice cloning and dynamic persona voice assets are not implemented by
this profile.

## Model loading

The worker verifies bounded regular, singly linked model/config files before
parsing them. Symlinks, FIFOs, oversize files and changed digests are rejected.
The operator's `MEET_PIPER_MODEL` path must be absolute; no request supplies a
path. The expected pins match the existing standalone provisioning script.
Only verified in-memory byte snapshots reach ONNX and `PiperConfig`, never
the subsequently mutable filenames. Thus replacing a file after hash checking
cannot swap the model actually parsed. The read-only model mount, container
memory limit and supervising subprocess deadline remain required safeguards.

CUDA must be available and active on the session. ONNX's automatic whole-session
fallback during `run()` is explicitly disabled; ordinary CPU shape operators
inside the CUDA graph are not a whole-model CPU fallback. Optional Arabic
diacritization is disabled for this fixed German voice, so it cannot trigger
an unrelated model download. Local model availability errors never activate a
cloud provider, model fetch or another voice.

## Hub, worker and result

Newly composed media-enabled Hubs inject the profile into each bounded turn;
`ANANTA_MEET_SPEECH_MAX_SECONDS` optionally reduces its default 40-second budget.
This flag is Hub configuration, not a caller-controlled field. The normal Hub
task stores the exact speech profile beside its existing lease/persona binding.
Current publication leases and terminal task CAS reject changed profile
bindings. The chat reply service supports the same injected execution profile;
this does not add the still-missing MDS chat/receive transport.

The worker returns optional `speech: {profile, samples}` metadata only for a
profile-bound request. The Hub checks exact request/result equality **and** the
decoded WAV's format, frame count, RIFF length and duration. A profile echo or
plausible duration field alone is insufficient. The sample budget is enforced
before speech output and again against the actual WAV; overlength is a bounded
failure rather than silently truncated success. This validates configuration
and bytes, not hardware attestation against a compromised worker.

The existing v1 request without `speech_profile` and result without `speech`
remain supported for older internal callers. New bound requests must not
downgrade when a worker omits the receipt. Upgrade workers before enabling the
new Hub composition, and drain old in-flight publication turns: the new Hub
will reject leases whose stored profile differs from its configured profile.
The unchanged public caller body still accepts text and the documented image/
persona options; it cannot add a speech profile or broaden voice rights.

Model pins/profile validation live in the dependency-light shared contracts;
filesystem admission, Piper inference and Hub WAV verification are separate
adapters (SRP/DIP). Existing Hub-to-worker contract imports remain preserved
coupling; moving the entire older turn envelope is a separate compatible
extraction, not needed for this addition.

Headless tests: `test_meet_speech_profile.py`, `test_meet_speech_binding.py`,
`test_meet_speech_output.py` and the opt-in private GPU profile gate. Synthetic
WAV fixtures test structure only. Real local Piper/RTX runs remain technical
observations, not production Registry evidence or proof of Meet reception.

Reference validation: 217 combined media/chat/profile tests in 141.88 seconds;
45 subsequent checks, including seven real private GPU/browser gates, in
57.14 seconds. The profile GPU gate binds both the actual persona selection
and a ten-second speech profile through a real Hub task and HTTP lease.
Private worker image:
`sha256:df416aee88cb3ff3799ff7772d94d4cafc026f37ced75a278ff6e47b1876ea49`.
