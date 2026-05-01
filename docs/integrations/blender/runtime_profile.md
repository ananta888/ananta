# Blender Runtime Profile

Blender runs as a thin Ananta client surface. Configure the addon with a hub endpoint, the `blender` profile, and a bearer token from environment or local addon preferences.

The hub owns goals, tasks, artifacts, policy, audit and approval decisions. The addon only captures bounded context, displays hub state and forwards explicit user decisions.

Default local development endpoint: `http://localhost:5000` with `allow_insecure_http=true`. Production profiles should use HTTPS and provide tokens through `ANANTA_BLENDER_TOKEN`.
