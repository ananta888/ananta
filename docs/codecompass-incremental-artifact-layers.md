# Incremental Artifact Layers

Stand: 2026-08-17 · Track: `codecompass-incremental-artifact-index-layer-sync`

CodeCompass indexes are an immutable **base** plus ordered **delta**
layers. Reads overlay newest-wins records and honor tombstones. The Hub
owns heads, generations and publish; workers only produce layer blobs.

```text
Snapshot A  -> Base layer
Snapshot B  -> Delta (upsert/tombstone)
Snapshot C  -> Delta
Compact     -> new Base, same effective view
```

Compatibility keys keep embedding/graph/FTS identities separate from
the human profile name. An embedding-model change rebases vectors
without silently mixing dimensions.

Operator surfaces:

- `GET /api/codecompass/layers/profiles`
- `GET /api/codecompass/layers/heads/<profile>`
- `POST /api/codecompass/layers/diff`
- `POST /api/codecompass/layers/update` (`dry_run` default true)
- `POST /api/codecompass/layers/compact`
- MCP `codecompass.layers_heads` / `codecompass.layers_plan`

## Chunk layers: commit → Hub → Worker → search (Stand 2026-09-26)

The `chunks` artifact kind is wired end to end. Every tracked text file of a
commit is indexed as citable chunks (Python by function/class/method,
Markdown by heading, everything else by line windows) with line ranges and
symbols; no file cap, large files are chunked instead of skipped.

```text
git commit ─► .git/hooks/post-commit (detached, never blocks the commit)
   scripts/codecompass_layer_sync.py: commit tree → redacted texts → snapshot manifest
   POST /api/codecompass/layer-sync/snapshots   → Hub names the contents it lacks
   POST /api/codecompass/layer-sync/content     → only those contents (≤ 16 MB batches)
   POST /api/codecompass/layer-sync/commits     → Hub plans: noop | delta | base
Hub: task codecompass_layer_build (ticket only) + RUN_* bound to the snapshot's SRC_*
Worker: GET job spec / content parts, ChunkLayerBuilder, PUT layer (verified address)
Hub: admits the bound result, advances the head (generation CAS), completes the run,
     updates the pointer row and the FTS projection
Search: KnowledgeIndexRetrievalService → pointer row → FTS candidates → existing scorer
```

* One build per profile at a time. A commit arriving meanwhile is remembered;
  after publish the Hub indexes the newest requested snapshot itself. A missed
  or failed commit heals with the next one: plans diff against the head.
* After `ANANTA_CODECOMPASS_LAYER_MAX_DELTAS` deltas (default 32) the next
  update is a new base built by a Worker (compaction by rebuild; the Hub does
  not merge layers itself).
* Storage (Hub only): `<data_dir>/codecompass_layers/{layers,heads,snapshots,
  content,dispatches,sync,search}`. Old layers and contents are not garbage
  collected yet.

### Flags (Hub)

| Variable | Default | Effect |
|---|---|---|
| `ANANTA_CODECOMPASS_LAYERS_ENABLED` | off | layer store, plans, search of pointer rows |
| `ANANTA_CODECOMPASS_LAYER_WRITES` | off | dispatch of builds (sync API returns errors without it) |
| `ANANTA_CODECOMPASS_LAYER_TENANT_ID` / `_PROJECT_ID` | `default` / `ananta` | evidence registry scope of the RUN_* |
| `ANANTA_CODECOMPASS_LAYER_MAX_DELTAS` | 32 | deltas before the next base |
| `ANANTA_CODECOMPASS_LAYER_TEAM_ID` | unset | team of the build tasks; a team-scoped autopilot only dispatches its team's tasks |

Workers register the `codecompass_layer_build` handler when Hub URL, token and
a complete worker identity are configured (scope `codecompass.layers.jobs`).

### Hook and first build

```bash
scripts/install_codecompass_layer_hook.sh      # only .git/hooks/post-commit, not core.hooksPath
python3 scripts/codecompass_layer_sync.py      # first run: base build of HEAD
tail -f .git/codecompass-layer-sync.log        # hook output
ANANTA_CODECOMPASS_LAYER_SYNC=0 git commit ... # skip the sync for one commit
```

Auth: `ANANTA_HUB_TOKEN`, or `INITIAL_ADMIN_USER` / `INITIAL_ADMIN_PASSWORD`
(environment or the checkout's `.env`); Hub `ANANTA_HUB_URL`
(default `http://localhost:5000`). The sync routes are Hub-admin only and
audited.

### Meet companion

The pointer row's `source_id` is `<profile>:chunks`, e.g.
`ANANTA_MEET_COMPANION_KNOWLEDGE_SOURCES='{"*": ["ananta-project:chunks"]}'`.
