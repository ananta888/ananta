# Local-only Modus

> TRANS-008 | Config | Status: open

## Zweck

Der `local_only`-Modus ist ein harter Betriebsmodus ohne externe Provider, ohne
Cloud-Modelle, ohne Remote-Indexierung und ohne Netzwerktools.

Er ist kein Sicherheitshack und kein Notfallmodus. Er ist ein Produktfeature
für Nutzer, die vollständige lokale Kontrolle benötigen: offline-Umgebungen,
regulierte Bereiche, sensitive Repositories, oder schlicht als bewusste Entscheidung
gegen Cloud-Abhängigkeit.

---

## Konfiguration

```yaml
safety_mode: local_only
network: deny_all
external_providers: false
remote_context: false
models: local_only
write_requires_approval: true
```

Diese Konfiguration wird beim Start validiert. Widersprüchliche Einstellungen
(z.B. `external_providers: true` bei `safety_mode: local_only`) werden abgelehnt,
nicht still überschrieben.

---

## Was blockiert wird

| Kategorie | Blockiert | Reason Code |
|---|---|---|
| Externe Modell-Provider | OpenAI, OpenRouter, Anthropic API, alle Remote-LLMs | `local_only_violation` |
| Externe Context-Provider | Augment, Remote-Indexierung, externe RAG-Dienste | `local_only_violation` |
| Netzwerk-Tools | HTTP-Requests, Web-Search, externe API-Calls | `local_only_violation` |
| Remote-Indexierung | Codebase-Index auf externem Server | `local_only_violation` |

Jeder blockierte Versuch erzeugt ein Audit-Event mit `reason_code: local_only_violation`
und der versuchten Zieladresse oder dem Provider-Namen.

---

## Was weiterhin erlaubt ist

| Kategorie | Erlaubt | Bedingung |
|---|---|---|
| Lokale Modelle | Ollama, LM Studio | wenn explizit in `models`-Config konfiguriert |
| Lokale Worker | Alle Worker ohne Netzwerkzugriff | Standard |
| CodeCompass | Lokale Indexierung und Retrieval | Standard |
| Datei-Lesen | Lesen innerhalb `allowed_paths` | Standard |
| Lokale Datenbank | SQLite, lokale DB-Verbindungen | Standard |

Ollama und LM Studio sind in `local_only` nur aktiv, wenn die jeweilige
Server-Adresse explizit auf `localhost` oder `127.0.0.1` konfiguriert ist.
Remote-Adressen werden abgelehnt.

---

## Sichtbarkeit

Der `local_only`-Status ist kein verstecktes Config-Flag.

- **CLI**: Jeder Start gibt `[LOCAL ONLY]`-Banner aus. Jede blockierte externe
  Aktion wird mit Klartext-Meldung abgebrochen, nicht still ignoriert.
- **TUI/UI**: Permanente Statusanzeige in der Kopfzeile oder Statusleiste,
  solange `local_only` aktiv ist.
- **API**: Alle API-Antworten enthalten `safety_mode: local_only` im
  Meta-Header, wenn der Modus aktiv ist.

Der Modus kann nicht über API oder Worker-Requests deaktiviert werden.
Änderungen an `safety_mode` erfordern Neustart mit geänderter Konfigurationsdatei.

---

## Audit

Jeder Versuch, im `local_only`-Modus auf externe Ressourcen zuzugreifen, erzeugt:

```json
{
  "event_type": "local_only_violation",
  "run_id": "<run_id>",
  "worker_id": "<worker_id>",
  "attempted_target": "<provider_name_or_url>",
  "reason_code": "local_only_violation",
  "action": "blocked",
  "timestamp": "<iso8601>"
}
```

Das Event wird in das AuditTrail geschrieben und im Run Report unter
`blocked_actions` aufgelistet.

---

## Tests

Die folgenden Test-Szenarien müssen abgedeckt sein:

| Test | Erwartetes Ergebnis |
|---|---|
| `external_providers: true` bei `safety_mode: local_only` | Konfigurationsfehler beim Start |
| Modell-Aufruf gegen OpenAI-Endpoint | Blockade + Audit-Event |
| AugmentContextProvider bei `local_only` | Blockade + Audit-Event |
| HTTP-Tool-Call bei `network: deny_all` | Blockade + Audit-Event |
| Ollama auf `localhost:11434` bei `local_only` | Erlaubt (wenn konfiguriert) |
| Ollama auf Remote-IP bei `local_only` | Blockade + Audit-Event |
| CLI-Start mit `local_only` | `[LOCAL ONLY]`-Banner sichtbar |
| Worker-Request zur Modus-Deaktivierung | Abgelehnt |

---

## Weiterführend

- `docs/architecture/transparency-and-local-safety.md` — Transparenz-Manifest
- `docs/architecture/verification-bundle.md` — Export ohne externe Abhängigkeiten
