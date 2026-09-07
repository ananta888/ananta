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

### Voice metadata foundation

`ananta_contracts.persona_voice` defines a canonical descriptor of at most
2,048 bytes: a schema and the exact catalog-pinned speech profile. Duplicate
keys, alternative encodings, unknown fields/models and caller-selected runtime
budgets are rejected. The descriptor is not PCM, a model upload or a preview
recording. Its source hash and immutable asset hash bind the same exact bytes.

Voice policies use explicit `media_kind: voice`, `licensed_pack` origin and
separate `persona_voice` source, license and (when applicable) consent proofs.
The SQL policy and asset namespaces are independent of images, videos and ASR.
The one-part `persona_media_voice` artifact is hidden on generic artifact
surfaces. Transitions validate the format's non-empty unique parts and still
roll back state/audit if an artifact or version is missing.

Initial headless foundation acceptance: 53 descriptor/policy/visibility tests
and 27 voice/video catalog tests passed. Catalog tests reserve real test-only
Hub identities; they deliberately do not claim completed inspection runs.
This foundation is not yet wired into public admission, worker inspection,
profile resolution or live publication. Those require the following execution
and receipt slice before any feature can be enabled.

The next slice uses `persona_voice_inspection` in the existing Hub task queue,
pre-reserved Registry runs and exact completion receipts. An isolated worker
validates canonical metadata only: it neither loads a model nor infers a license.
Its own request/lease signature domains, replay table and 20-second assignment
limit remain separate from image/video inspection. Storage revalidates bytes
and authority before/after access; revocation remains terminal and auditable.
This slice will be tested with real Hub tasks/Registry state and explicit
in-process transport fixtures before adding an opt-in HTTP/container composition.

The execution and storage slice now passes those checks: 17 initial task/asset
tests, 17 worker/storage tests, 128 image/video regressions (55.94 seconds with
two pytest workers), and 49 final cross-kind/lease regressions (29.69 seconds).
The latter include an additional check that a live voice callback cannot use an
image policy service. Eight actual signed HTTP tests passed in 15.65 seconds,
covering current Hub authority, revocation, durable replay after executor
recreation, separate request/lease domains and malformed request rejection.
All identities remain explicitly test-only. Storage preview returns descriptor
metadata, not audible preview or a publication grant.

The remaining stage-4 composition must add an isolated descriptor-worker image
and disposable private-container test, then retention/erasure and opt-in Hub/API
wiring. It must not enable ingestion without a retirement path or infer a
serving deployment from a successful local fixture.

### Isolated worker and retirement acceptance

The descriptor worker now has a pinned standard-library-only Python image and
an opt-in private Compose template: non-root, read-only root filesystem, 128 MB,
0.5 CPU, 32 PIDs, no capabilities, host ports, GPU/model mounts or publication
credentials. The actual private-container HTTP test passed in 12.14 seconds
using image `sha256:e7d88d03876c95fbe76e151ce888c621c07099267602f6d700a667c331ef1018`.
Real Hub project authority and test-only Registry receipts admitted the exact
descriptor; subsequent policy revocation denied its release. Disposable worker
and private network were removed; no serving deployment was modified.

Voice retirement uses its own ledger, SQL index and `persona_voice_retention`
Hub task. It removes only the exact hash-bound `v0001__voice.json` after a durable
retirement tombstone. Models, keys and unrelated files are not targets. Changed
bytes, symlinks/hardlinks, revoked/cancelled claims fail closed. Scheduled work
is separately opt-in via `ANANTA_PERSONA_VOICE_RETENTION_ENABLED=1`, Hub-owned
and stoppable. Nine new retirement/template tests passed in 13.30 seconds;
52 existing image/video retirement regressions passed in 28.79 seconds with
two pytest workers.
This is logical file deletion, not guaranteed secure erasure of storage devices.

Before opting in, the remaining composition slice must provide authenticated
voice-policy/admission/reference/query/retirement API routes and connect the
voice reference port to persona profiles. A saved profile or descriptor preview
still must not activate a live microphone or grant publication. Existing
image/video profile API shapes must remain compatible.

### Authenticated API, discovery and profile composition

Voice-only bootstrap is now opt-in via `ANANTA_PERSONA_VOICES_ENABLED=1`; all
operator execution bindings are validated before installing its services.
`docker-compose.persona-voices-hub.yml` supplies only the independent private
worker/key wiring, with both inspection and retirement disabled by default.
No policy is automatically installed and no key is created by bootstrap.

Under `/api/persona-media/v1/projects/<project>`, the additive routes are:

- `PUT voice-policy` / `DELETE voice-policy/<source_id>` for explicit terms/CAS;
- `POST voices` for a bounded base64 canonical descriptor and registered pins;
- `POST voices/query` and `GET voices/<id>/reference` for scoped metadata;
- `GET voices/<id>/preview` for the descriptor JSON only, never PCM;
- `DELETE voices/<id>` followed by explicit `POST voices/<id>/purge`, or
  `PUT/GET/DELETE voices/<id>/retention` for bounded scheduled retirement.

All user routes require user authentication and current project/policy checks.
There is no synthesis, publication, model-download or generic artifact escape.
Voice paging handles are independent, expiring and tenant/project/subject-bound.
Organization/team/agent profiles now accept the optional voice reference port;
inheritance, disabling, stale pins and revoked receipts are checked without
silently substituting another voice. `for_voice_execution` resolves only the
exact selected metadata; publication still needs the separate Meet adapter.

Acceptance: 24 voice bootstrap/profile tests (19.95 seconds), 22 actual
task/storage-backed voice API tests (18.38 seconds), and 126 voice-query plus
existing image/video/profile/API regressions (54.22 seconds), each with two
pytest workers. SRP/DIP keep lifecycle, reference resolution, authenticated
routes and composition separate. Existing explicit kind allowlists are
extended additively; no broad central profile refactor was introduced.
Stages 5–6 (live selection CAS, old speech invalidation and UI/delivery) remain
open. None of this enables the serving instance automatically.

## Live voice selection: next closed slices

First add a Meet-specific adapter that resolves a current voice profile,
rechecks explicit preview/publication policy and the completed inspection
receipt, reads the exact immutable descriptor, and derives only the catalog's
speech profile. The profile pin and asset reference remain Hub metadata; a
worker gets neither authority to choose another persona nor source-policy data.

Then add optional dialog negotiation and a passive voice-selection CAS that
advances only the global and speech revisions while preserving enabled state.
The exchange must expose a bounded independent ready/paused/blocked projection.
Policy revocation must invalidate playing/queued old speech even if the
operator has not changed controls. Generation must recheck the selected pin
before dispatch, during capacity admission and before releasing PCM. Existing
unnegotiated sessions retain their fixed operator profile and exact old wire
shape. A voice switch must not revoke parent Meet membership or unrelated
avatar/screen/chat. No live selector/API/UI is enabled until these fences agree.

The spoken binding extension will carry paired selection/profile digests only
for explicitly negotiated voice sessions. The ordinary signed response must
match both digests and its actual speech receipt. The worker additionally
tracks observed voice-state transitions locally: a blocked-to-ready round trip
cannot revive a previously pending reply just because its pins look unchanged.
This local fence is not a new Hub identity or policy grant. Legacy spoken
bindings and unnegotiated fixed-voice behavior remain unchanged.

The Meet descriptor/profile adapter slice passed 50 combined adapter regressions
in 30.12 seconds. The passive voice-selection CAS passed 81 voice/avatar/control
tests in 37.35 seconds, including preserving both initially enabled and paused
speech. Three initial failures were corrected test-fixture assumptions: the
shared fixture started speech enabled. Production activation semantics were
not weakened. These Hub primitives are not yet exposed as a live selector;
negotiated projections and pending/playing result fences are the next slice.

The projection and pending/playing fences now pass 101 focused contract/worker
tests (42.83 seconds); selected-voice Hub generation and legacy reply/binding
regressions pass 38 tests (23.23 seconds). Per-call profiles do not mutate the
shared configured default. Only Hub child-task metadata contains the selected
persona pins; delegated generation receives the closed speech profile. Current
policy is rechecked around admission and PCM release, with a post-I/O source
race check. These are headless technical tests, not production release evidence.

Live wiring now requires the explicit optional `voice_profiles: true` start and
assignment field, `speech.publish`, a configured operator speech budget and the
voice-profile adapter. Old sessions reject upgrades and keep their exact wire
shape. `PUT /api/meet/v1/projects/<project>/dialogs/<task>/voice` accepts only an
owner-authenticated, bounded selection CAS. It never activates paused speech.
The Hub exchange includes `voice` only for negotiated sessions; the worker
requires its exact closed shape and matching speech revision. Voice-policy
revocation yields an independent blocked projection without cancelling chat,
avatar, screen or parent membership. Bootstrap composes the adapter only when
the independent persona voice/profile services are present.

This wiring passed 67 focused tests in 38.36 seconds, including actual SQL Hub
tasks, signed callback HTTP, authenticated selection routes, concurrent source
changes, composition and legacy avatar/transport regressions. UI selection and
cross-repository delivery of selected voices remain subsequent checks. No
serving configuration, key, model or trust policy was changed by this wiring.

The next UI slice adds a separate default-off voice-profile negotiation option
and owner-scoped metadata picker. Image and voice candidates retain independent
validators; preview metadata is never treated as publication authority. A small
shared candidate controller will own cancellation, bounded requests and stale
scope fencing for both pickers, while each component owns its labels and media
semantics (SRP/composition, no component inheritance). The UI sends only the
current profile pin plus expected control revision, or explicit configured-voice
selection. It cannot choose URLs, speaker IDs, model paths or activate speech
as a side effect. Project/identity changes clear candidates and pending work.

After live wiring, 129 spoken reply/worker/client/SQL chat regressions passed in
52.57 seconds, including actual Hub child-task current/terminal CAS rejection
when the selected voice metadata changes. No interactive approval was used.

The independent Angular voice picker and explicit negotiation now pass all 96
Meet UI tests (1.67 seconds), targeted ESLint and Angular compilation. A new
timeout test found that asking for a nonexistent candidate cleared the failure
diagnostic; the controller now preserves it. The existing unrelated
KnowledgeHygienePage RouterLink warning remains. Candidate lifetime/cancellation
is shared by composition, while media validators remain deliberately separate.

Next add isolated cross-repository voice scenarios: real Hub Task selection and
signed exchange/bindings with explicit synthetic profile policy, separate
synthetic long-PCM interruption and actual pinned local GPU neutral/whisper
delivery. Observe current Worker projections and remote non-silent audio, never
substitute observations for grants. Own and clean up only private fixture
containers/keys/networks. The existing large cross-repository setup function is
a preserved SRP limitation; new scenario behavior belongs in a separate helper,
not more scenario branches inside its orchestration fixture. These runs do not
claim public TURN, production persona admission or release identity.
