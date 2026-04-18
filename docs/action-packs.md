# Action Packs

Action Packs sind kontrollierte Buendel von Faehigkeiten (Capabilities), die in Ananta zur Verfuegung stehen. Sie dienen dazu, Faehigkeiten nicht als lose Toolsammlung, sondern als governance-faehige Einheiten zu verwalten.

## Konzept

Ein Action Pack gruppiert mehrere technische Capabilities (z. B. `file_read`, `file_write`) zu einer logischen Einheit (z. B. `file`). Jedes Action Pack kann:

- Global aktiviert oder deaktiviert werden.
- Ueber Policies gesteuert werden.
- In Audit-Logs nachverfolgt werden.

## Standard Action Packs

Ananta wird mit folgenden Standard Action Packs ausgeliefert:

| Name | Beschreibung | Capabilities | Standardmäßig Aktiv |
| :--- | :--- | :--- | :--- |
| `file` | Datei-Operationen | `file_read`, `file_write`, `file_patch` | Ja |
| `git` | Git-Operationen | `git_status`, `git_diff`, `git_commit` | Ja |
| `shell` | Shell-Kommandoausfuehrung | `shell_exec` | Nein (Hochrisiko) |
| `browser` | Web-Recherche | `web_search`, `web_fetch` | Nein |
| `document` | Dokument-Verarbeitung | `doc_extract`, `doc_convert` | Ja |

## Konfiguration

Action Packs koennen ueber die `config.json` oder Umgebungsvariablen konfiguriert werden.

Beispiel `config.json`:

```json
{
  "action_packs": {
    "shell": {
      "enabled": true
    },
    "file": {
      "enabled": true
    }
  }
}
```

## Governance und Security

Action Packs sind in das Platform Governance Modell integriert. Bevor eine Capability eines Packs ausgefuehrt wird, prueft der `PlatformGovernanceService`, ob das entsprechende Pack aktiviert ist.

Besonders riskante Packs wie `shell` sind standardmaessig deaktiviert und erfordern eine explizite Freischaltung.

## Erweiterung

Neue Action Packs koennen ueber Plugins registriert werden. Ein Plugin kann beim Start den `ActionPackService` nutzen, um neue Packs in der Datenbank zu hinterlegen.
