# Local game dragon adapter

`POST /api/game-dragon/decide`, `/design`, `/transcribe` and `/speak` expose a bounded local
game capability. They are disabled unless `/app/data/game-dragon.json` enables
them (override: `ANANTA_GAME_DRAGON_CONFIG`). A separate bearer token from
`token_file` is required; existing admin credentials are not sent to the game.

Configuration keys: `enabled`, `token_file`, `decision_url`, `voice_url`,
`voice_token_file`. Both runtime URLs must be local HTTP endpoints. The configured
voice runtime receives its own token. Do not put tokens in browser code or source.

The Hub owns the fixed Jev schema: four moods, three small animation gestures,
and at most 100 generated speech tokens. The client supplies bounded world facts,
a message and up to six short conversation entries. There are no model-selected
URLs, commands, worker calls or independent orchestration loops. This is a
synchronous inference adapter, following the existing voice/decision API pattern;
it does not create autonomous Hub tasks. Any future tool use or autonomous world
editing belongs in the existing Hub task queue and worker dispatch flow.

Two concurrent Hub requests are allowed per process. JSON requests are limited
to 16 KiB and speech input to 2 MiB. Slow inference has bounded timeouts. The
game runs decisions asynchronously from its simulation. Whisper and Piper run
outside the Hub process, as does the local Jev inference server. The speech
runtime reports the actual transcription device; the Hub preserves that label.

Structure: route handlers own HTTP authorization and request limits; the domain
adapter validates context/output and uses an injected HTTP transport. SRP keeps
rendering, flight simulation and audio decoding outside Ananta. The deliberately
small adapter combines four related game endpoints; it does not alter the
existing general-purpose voice provider or CLI backend subsystem.

## Creature design proposals

`/design` uses the same dedicated credentials, concurrency limit and 16 KiB JSON
budget. `CreaturePlanner` is a separate domain service with an injected bounded
decision transport. It sends the fixed Jev tree schema to `decision_url`; it
cannot execute edits, publish assets or read files from the caller's request.

Edit requests contain `schema_version: "1.0"`, an `instruction` (up to 1500
characters) and `context.selected_parts` (1–16 IDs, finite bounding boxes,
lock/mask information). The model chooses one allowed region, operation and
strength. The Hub derives the spatial brush parameters from those bounds.
Adding a horn/wing to a masked region is rejected. Orbit independently validates
the proposal, calculates candidate geometry and requires an explicit acceptance.

Generation requests add `mode: "generate"`. The response chooses one of eight
original procedural templates and bounded anatomical parameters. This is
model-planned parametric generation, not unrestricted neural mesh generation.
No model weights, proprietary models or Orbit geometry are distributed here.

Self-contained verification:
`python -m unittest tests.test_game_dragon tests.test_game_creature -v`.
Fixtures are synthetic and make no network/model calls. Live probes are technical
observations, not Hub-issued production release evidence.
