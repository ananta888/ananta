# Local RTX3080 + Free Cloud + MiniMax M3

This profile is intended for running Ananta without a smartphone worker.

## Purpose

- Framework 16 runs the controller, UI, CodeCompass, RAG, tests, and orchestration support.
- RTX 3080 runs local models through Ollama or LM Studio.
- Free cloud models are used for non-sensitive tasks when they are useful and available.
- MiniMax M3 is reserved for hard cases, long-context analysis, stuck agents, and architecture reviews.
- Fairphone 6 is explicitly disabled in this profile.

## Routing idea

```text
secret/private/sensitive  -> local only
deterministic task        -> tool only, no LLM
normal task               -> free cloud first, then local fallback
coding/private code       -> local first, cloud only with approval
architecture review       -> free cloud, then MiniMax M3
hard case                 -> MiniMax M3
paid cloud                -> manual approval only
```

## Files

- `profile.json` defines profile metadata and enabled device classes.
- `models.json` defines logical model entries and roles.
- `providers.json` defines provider endpoints and secret environment variables.
- `routing-policy.json` defines routing modes per task category.
- `secrets.example.env` documents required secret variables without storing real secrets.
- `prompts/` contains profile-specific system prompts.

## Secret handling

Do not commit real API keys.

Use this profile file as template:

```text
profiles/local-rtx3080-freecloud-minimax/secrets.example.env
```

Put real secrets outside the repository, for example:

```text
~/.ananta/secrets/local-rtx3080-freecloud-minimax.env
```

## Notes

This profile is intentionally additive. It does not change the hub-worker architecture. The hub remains the control plane and decides routing. Workers only execute delegated tasks.
