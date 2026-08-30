# Collaboration workspace operations

The native core is off by default. Enable it only on the Hub:

```text
ANANTA_COLLABORATION_WORKSPACE_ENABLED=true
ANANTA_COLLABORATION_WORKSPACE_STATE=data/collaboration-workspace.sqlite3
```

The additive API is `/api/collaboration/workspaces`. Disabling the flag removes the service while leaving
stored data and all legacy ShareSession paths untouched. Buzz is not required and its default adapter reports
`disabled`; it never changes native-core availability.

Run `python scripts/check_collaboration_workspace_boundaries.py` and
`pytest -q tests/collaboration_workspace` after changes. The suite is deterministic and requires no browser,
network, external relay, prompt, checkbox or person. The Angular route is `/collaboration` for authenticated
Hub users.

SQLite is an initial single-Hub adapter. Before production rollout, provide backup/restore, bounded outbox
delivery, projection rebuild/load evidence, retention/tombstones and a multi-instance persistence adapter.
