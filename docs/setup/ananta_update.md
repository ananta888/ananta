# ananta update

`ananta update` updates an existing Ananta checkout, refreshes dependencies, and runs a smoke check.

## Basic usage

```bash
ananta update --repo-dir "$HOME/ananta"
```

By default, the command:
1. detects repository state (branch/commit)
2. refuses dirty working trees
3. fetches and pulls with fast-forward rules
4. refreshes Python deps (`pip install -e .`)
5. runs a smoke check (`python -m agent.cli.main --help`)

## Options

```bash
ananta update --help
```

Key flags:
- `--repo-dir <path>`: explicit repository path
- `--ref <branch-or-tag>`: checkout this ref before update
- `--allow-dirty`: permit updates with local modifications
- `--venv <path>` or `--python <exe>`: choose runtime for dependency refresh
- `--skip-deps`: skip dependency refresh
- `--skip-smoke`: skip smoke check
- `--rollback-to <sha-or-tag>`: rollback checkout to prior ref

## Rollback guidance

After each successful update, the command prints:
- previous commit
- current commit
- a ready-to-run rollback command

Example:

```bash
ananta update --repo-dir "$HOME/ananta" --rollback-to <sha>
```

## Safety model

- No deletion of config, secrets, runtime profiles, or user data.
- Dirty trees are blocked unless explicitly overridden.
- Update flow uses explicit git operations with visible failure messages.
