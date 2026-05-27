# KRITIS Sandbox Filesystem Boundary Controls (K3-SBX-T04)

## Ziel

Der Sandbox-Betrieb erzwingt Dateisystemgrenzen für Terminal-/Wrapper-Zugriffe, damit Tasks nur innerhalb freigegebener Workspace-Roots arbeiten.

## Kontrollmodell

1. **Workspace Boundary Enforcement**: Pfade müssen unter `allowed_workspace_roots` liegen (Standard: `/workspace`, `/project-workspaces`).
2. **Path Traversal Block**: Relative Traversals (`..`) werden verworfen.
3. **Privileged Path Fragments Block**: Zugriff auf sensible Fragmente wie `/.ssh`, `/etc/`, `/proc/`, `/sys/` wird abgelehnt.
4. **SSH Wrapper Gate**: `ssh_terminal_wrapper._sanitize_path` setzt diese Regeln vor Session-Erstellung durch.

## Konfiguration

- `ANANTA_WORKSPACE_ROOTS` (CSV) überschreibt erlaubte Root-Pfade.
- `ANANTA_BLOCKED_PATH_FRAGMENTS` (CSV) überschreibt verbotene Fragmente.
- Ohne Overrides gelten sichere Defaults.

## Erwartete Wirkung

- Keine Terminal-Session mit Workspace-Pfad außerhalb des freigegebenen Sandbox-Bereichs.
- Kein stilles Durchreichen privilegierter Host-Pfade in den Session-Kontext.
