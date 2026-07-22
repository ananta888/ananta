# Runtime-only Redis material

This directory intentionally contains no credential or certificate fixture.
Before the `livekit_native_distributed` profile can start, provision:

- `server.crt`
- `server.key`
- `ca.crt`
- `users.acl` with separate least-privilege `livekit` and health identities

Keep all four files untracked. The missing files are a deliberate fail-closed default.
