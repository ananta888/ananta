# Ananta Mobile Security Model

Date: 2026-04-30
Scope: ANM-092

## Kernprinzipien

- Default-Deny fuer riskante Runtime-Aktionen
- Least-Privilege bei Filesystem- und Netzwerkzugriff
- Explizite Nutzerbestaetigung fuer sensible Operationen
- Auditierbarkeit aller Allow/Deny-Entscheidungen

## Enforcement-Punkte

- `PermissionBroker` erlaubt nur bekannte Aktionen und nur mit Confirmation.
- Filesystem-Sandbox: nur app-lokale Pfade (`filesDir`, `cacheDir`, `codeCacheDir`).
- Netzwerk-Policy: nur `https` plus erlaubte Host-Suffixe.
- Prompt-Injection-Grenzen im `MobileAgentRuntimeAdapterService`.
- Tool-Aufrufe aus Modellanfragen werden geblockt.

## Always-Listening Schutz

- Live-Modus startet nur explizit.
- Sicherheitslimit `LIVE_SESSION_MAX_SECONDS` erzwingt Stop.
- Kein impliziter Hintergrund-Dauerbetrieb.

## Audit

- Entscheidungen werden in `files/voxtral/audit.log` protokolliert.
