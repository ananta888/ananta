# CLI-Backend Betriebsmodi: Windows / WSL2 / Docker (COMMON-004)

Stand: 2026-07-05 · Track: `todo.subscription-cli-adapters-codex-claude` · COMMON-004

Diese Doku beschreibt, **wo** die Subscription-CLI-Backends (OpenAI Codex CLI,
Claude Code CLI) laufen, wo ihre Login-Daten leben und was Ananta in jedem
Betriebsmodus sehen kann. Grundregel: **Ananta liest niemals Credential-Dateien**
(`~/.codex/`, `~/.claude/`); es prüft nur Binary-Verfügbarkeit (`shutil.which`)
und reicht CLI-Fehlermeldungen durch.

## Modus 1: Windows nativ

- Ananta-Hub und CLIs laufen beide direkt unter Windows.
- `codex` / `claude` müssen im `PATH` des Hub-Prozesses liegen
  (`npm i -g @openai/codex`, `npm i -g @anthropic-ai/claude-code`).
- Login-Daten liegen unter `%USERPROFILE%\.codex\` bzw. `%USERPROFILE%\.claude\`
  und werden ausschließlich vom jeweiligen CLI verwaltet.
- Preflight (`GET /api/sgpt/backends/{id}/health`) zeigt `not_installed`,
  wenn das Binary im Hub-PATH fehlt — auch wenn es in einer anderen Shell
  (z.B. Git Bash mit eigenem PATH) funktioniert.

## Modus 2: WSL2 local-dev (der typische Entwickler-Setup)

- Hub läuft in WSL2, CLIs sind in WSL2 installiert (`npm i -g …` innerhalb WSL).
- Logins werden **in WSL** durchgeführt (`codex login`, `claude login` im
  WSL-Terminal); die Sessions liegen unter `~/.codex/` / `~/.claude/` im
  Linux-Home.
- Achtung Doppel-Installation: ein unter Windows installiertes `claude.exe`
  ist über `/mnt/c/...` u.U. im WSL-PATH sichtbar, nutzt aber die
  **Windows**-Login-Session. `which codex` / `which claude` in WSL zeigt,
  welches Binary wirklich gefunden wird — dasselbe Ergebnis liefert der
  Diagnose-Endpunkt (`POST /api/sgpt/backends/{id}/diagnose`).
- Für Codex mit lokalem OpenAI-kompatiblem Target (LM Studio auf dem
  Windows-Host): `localhost` funktioniert in WSL2 nur mit aktiviertem
  localhost-Forwarding; sonst Host-IP aus `/etc/resolv.conf` verwenden
  (siehe `docs/integrations/cliproxyapi.md`, Troubleshooting).

## Modus 3: Docker-Fullstack

- Läuft der Hub im Container, sind Host-CLIs **nicht erreichbar**:
  `shutil.which("claude")` findet im Container nichts → Preflight meldet
  ehrlich `not_installed` statt eines Fake-ready. Die UI-Karte zeigt den
  Install-Hint; ein Test-Run schlägt mit klarer Diagnose fehl.
- **Kein Default-Mount von Credential-Verzeichnissen:** `~/.codex/` und
  `~/.claude/` werden bewusst nicht in Container gemountet. Wer das
  trotzdem tut, verschiebt die Vertrauensgrenze (Container-Prozesse können
  dann mit dem Account des Hosts agieren) und muss das als bewusste
  Deployment-Entscheidung dokumentieren.
- CLI-in-Container ist möglich (Binary im Image installieren und
  `codex login` / `claude login` interaktiv im Container ausführen),
  aber die Session lebt dann im Container-Filesystem und stirbt mit ihm,
  sofern kein dediziertes Volume existiert.

## Diagnose statt Fake-ready

Der Status in UI/Preflight ist bewusst konservativ:

| Status | Bedeutung | Quelle |
|---|---|---|
| `not_installed` | Binary nicht im PATH des Hub-Prozesses | `shutil.which` |
| `disabled` | `claude_cli.enabled=false` (opt-in nicht gesetzt) | agent config |
| `ready` | Binary gefunden, Konfiguration plausibel | Preflight |
| `not_logged_in` | CLI bricht beim Run mit Login-Fehler ab | stderr des Runs |

`not_logged_in` wird **nicht** vorab geprobt (das hieße Token-Dateien lesen
oder einen Netz-Roundtrip erzwingen); es zeigt sich beim Diagnose-/Test-Run
und wird als Fehlertext durchgereicht. Nächste sichere Aktion ist immer der
angezeigte `login_command` im **lokalen Terminal des Nutzers**.

## write_armed / Diff-Review (schreibende Claude-Runs)

Schreibende Runs laufen ausschließlich über `run_claude_write_armed`
(`POST /api/sgpt/backends/claude_code/write-armed-run`):

1. Das `workdir` muss ein **Git-Repo innerhalb von
   `claude_cli.allowed_paths`** sein (doppeltes Opt-in: enabled + Pfadliste).
2. Es wird in einen **isolierten Temp-Workspace kopiert** und mit einem
   Baseline-Commit eingefroren; Claude läuft nur dort mit
   `--permission-mode acceptEdits` (der einzige erlaubte Kontext für
   diesen Modus — der normale Run-Pfad bleibt auf `plan`/`default`).
3. Ergebnis ist ein **Diff-Artefakt** (`git diff` + Dateiliste) mit Status
   `awaiting_diff_review`. Der Diff wird **nie automatisch angewendet**;
   die Übernahme ins Originalprojekt ist eine manuelle Review-Entscheidung
   (Approval-Gate). Der Temp-Workspace wird danach gelöscht.

Hinweis: der Workspace wird 1:1 kopiert — für sehr große Repos/Monorepos
ist dieser Pfad ungeeignet.

## Folgearbeit (bewusst nicht gebaut)

- **Local-runner/Bridge** für Docker-Fullstack (Hub im Container delegiert
  CLI-Runs an einen Runner auf dem Host): erst bauen, wenn der Docker-Modus
  mit Subscription-CLIs wirklich gebraucht wird.
- **Diff-Apply-Endpunkt** (reviewten Diff serverseitig anwenden): bis dahin
  wendet der Nutzer den Diff bewusst selbst an (`git apply`).
