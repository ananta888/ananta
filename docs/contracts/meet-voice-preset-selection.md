# Hub-owned local voice and persona selection

## Source audit and scope before implementation

MAP-19/20/22 still lack dynamic voice/profile selection. The existing Piper
adapter implements one pinned German voice, and live spoken replies use the
operator's fixed profile. Persona image/video asset admission, immutable Hub
evidence bindings, profile inheritance and independent speech revisions already
exist. Existing speech correction/ASR datasets are not TTS voices or cloning
permissions. Nothing in this plan enables uploaded voice cloning.

The first additional local preset family is `thorsten_emotional/medium`:
eight expression variants of one German speaker, not eight distinct speakers.
The authoritative [model card](https://huggingface.co/rhasspy/piper-voices/blob/1162a9173d0ce503555aed757976b7a9912eae4c/de/de_DE/thorsten_emotional/medium/MODEL_CARD)
names CC0 training data from [Thorsten-Voice](https://github.com/thorstenMueller/Thorsten-Voice).
The pinned configuration specifies 22,050 Hz and speaker IDs amused=0, angry=1,
disgusted=2, drunk=3, neutral=4, sleepy=5, surprised=6 and whisper=7. These upstream
names describe synthesis choices, not inferred emotions or a user's condition.
Model repository licensing and dataset terms must remain separately recorded;
a repository-level MIT badge is not sufficient proof for every voice dataset.

Hugging Face's tree for immutable revision
`1162a9173d0ce503555aed757976b7a9912eae4c` reports a 76,745,905-byte model with
LFS SHA256 `c1764e652266cd6dcebf1b95c61973df5970a5f5272e94b655ff1ddf9a99d1ff`.
Download and hash verification must precede activation. The existing model's
70-MB bound remains unchanged; a recognized new preset gets its own exact
bounded file specification. No arbitrary caller model path, URL or speaker
index may cross the assignment boundary.

## Ordered implementation and acceptance

1. Extract an immutable local voice catalog from the single hard-coded profile
   while preserving the exact default wire projection and exported compatibility
   constants. A new preset binds language, immutable model/config hashes and a
   catalog-owned speaker variant; arbitrary mixtures and unknown IDs fail closed.
2. Separate model-only provisioning from optional key creation. Explicit model
   selection downloads only pinned bounded artifacts and never rotates keys,
   edits serving configuration or starts a service. Use verified snapshots and
   per-model limits before parsing, and pass only a catalog-owned speaker choice
   to Piper. Keep CUDA/provider/resource/PCM limits and no-cloud behavior.
3. Add deterministic profile/catalog/loader/variant tests and a real isolated
   RTX3080 synthesis/stop test with two selected variants. A model file or green
   fake alone does not prove GPU speech. No arbitrary quality/identity claim.
4. Add a separately typed voice-preset asset/policy domain using existing Hub
   source/license/consent registration and ordinary inspection Task/dispatch
   receipts. Voice descriptors are bounded closed metadata, not uploaded models.
   Profile lookup must use this domain rather than treating image or ASR
   permissions as voice permission. Missing/disabled/revoked voices fail closed.
5. Resolve an admitted organization/team/agent voice pin in the Hub. Give live
   voice changes their own negotiated selection CAS and speech revision; recheck
   policy before dispatch and release, invalidate queued/playing old speech,
   preserve independent avatar/screen/chat, and never choose a fallback silently.
6. Reuse profile UI/API patterns for passive voice selection and explicitly
   distinguish preview, metadata, model availability and publication. Verify
   headless old/new compatibility, foreign/stale selections, revocation during
   generation, actual selected-voice delivery and independent multiple sessions.

Each stage is additive and separately committed/tested. SRP/DIP keep catalog,
artifact provisioning, model loading, asset admission, profile policy, Hub CAS
and browser playback separate. Workers execute assignments, never select new
personas, issue evidence identities or orchestrate further work. This remains
implementation work until verified; public deployment and production release
evidence require their own actual authority and pre-reserved Hub runs.

## Verified local preset slice (stages 1–3)

The shipped immutable catalog now supports the original default and all eight
expression variants. The default profile's wire fields and exported model pins
are unchanged. The worker verifies each model/config snapshot before parsing,
checks the complete multi-speaker map, and passes only the catalog's speaker ID
to Piper. CUDA remains required; there is no cloud or CPU fallback.

Model-only provisioning is separate from identity setup:

```sh
python -m scripts.provision_meet_voice /absolute/models/directory \
  --voice-id piper.de_DE.thorsten_emotional.medium.neutral
```

This command verifies pinned bounded downloads, preserves existing files and
creates no keys or service configuration. Additional presets use the explicit
`MEET_PIPER_MODELS_DIR` (default `/models`); the legacy `MEET_PIPER_MODEL` remains
an override for the original voice only. A legacy fixed path without an explicit
directory cannot silently select another model.

Headless acceptance: 109 catalog/profile/output/binding/fixture regressions
passed in 73.33 seconds; 19 provisioning tests passed in 18.40 seconds. The
opt-in `MEET_VOICE_VARIANT_GPU_GATE=1` test passed in 13.15 seconds on the local
RTX3080: neutral (speaker 4) and whisper (speaker 7) produced non-silent PCM,
with first frames including loading at 1,474/880 ms and local checkpoint stops
at 24/26 ms. These are local technical observations, not voice quality claims,
persona policy grants, Meet delivery proof or production release evidence.

Stages 4–6 remain open. Their first slice is a closed, one-part voice descriptor
asset and an explicitly separate voice policy domain. The shared SQL catalog's
hard-coded two-part transition count must become format-derived without
weakening completeness checks for image/video (LSP/OCP). No fabricated preview
artifact, uploaded model or implicit image/ASR permission will stand in for a
voice asset.
