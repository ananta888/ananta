# Bootstrap install (one-file installer)

This path is for users who want a working `ananta` command quickly without learning venv or Docker first.

Important:
- Local CLI usage does **not** require Docker.
- The installer checks prerequisites and shows hints; it does not auto-install Docker, Podman, or cloud providers.
- Runtime setup still happens with `ananta init`.

## Script paths

- Windows installer: `scripts/install-ananta.ps1`
- Linux/macOS installer: `scripts/install-ananta.sh`

## Windows 11 (PowerShell)

One-command (download + run):

```powershell
iwr https://raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.ps1 -OutFile install-ananta.ps1; .\install-ananta.ps1
```

Safer variant (download, inspect, then run):

```powershell
iwr https://raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.ps1 -OutFile install-ananta.ps1
Get-Content .\install-ananta.ps1
.\install-ananta.ps1 -InstallDir "$HOME\ananta" -Ref "main"
```

## Ubuntu/Linux and macOS (Bash)

One-command (download + run):

```bash
curl -fsSL https://raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.sh -o install-ananta.sh && bash install-ananta.sh
```

Safer variant (download, inspect, then run):

```bash
curl -fsSL https://raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.sh -o install-ananta.sh
sed -n '1,200p' install-ananta.sh
bash install-ananta.sh --install-dir "$HOME/ananta" --ref main
```

## Installer options

- `--install-dir <path>`: choose install location
- `--ref <branch-or-tag>`: install/update from branch/tag/ref (default `main`)
- `--allow-dirty`: allow update on dirty existing checkout (otherwise refused)

Both installers are idempotent:
- existing checkout is updated safely
- `.venv` is reused
- installer reruns align with `ananta update` behavior

## Next steps after install

Use your venv Python (or `ananta` if on PATH):

```bash
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
ananta doctor
ananta first-run
# requires running hub + matching credentials
ANANTA_BASE_URL=http://localhost:5000 ANANTA_USER=admin ANANTA_PASSWORD=<password> ananta status
```

If you started a local hub via `docs/setup/quickstart.md` with
`INITIAL_ADMIN_PASSWORD=ananta-local-dev-admin`, use that same value as
`ANANTA_PASSWORD`.

OpenAI-compatible runtime example:

```bash
ananta init --yes --runtime-mode local-dev --llm-backend openai-compatible --endpoint-url http://localhost:1234/v1 --model your-model
```

The installer never stores API keys for you; configure credentials in your shell/profile.

## Update and rollback

Use the built-in update command for normal upgrades:

```bash
ananta update --help
ananta update --repo-dir "$HOME/ananta"
```

Rollback to a previous commit if needed:

```bash
ananta update --repo-dir "$HOME/ananta" --rollback-to <sha>
```

When to rerun installer vs `ananta update`:
- Use `ananta update` for normal updates.
- Rerun installer when initial setup is broken/missing or you need to repair venv/bootstrap state.
