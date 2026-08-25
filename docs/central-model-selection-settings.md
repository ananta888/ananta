# Zentrale Modellwahl in Ananta

## Zweck und Architekturgrenze

Die Einstellungsseite **Modelle** ist die kanonische Administrationsoberfläche
für Inventar, Profile, Consumer-Zuweisungen, Fallbacks, Cognitive Styles und
Migration. Der Browser kommuniziert ausschließlich mit dem Hub. Er spricht
weder Ollama oder LM Studio noch OpenRouter oder einen CLI-Prozess direkt an.

Der Hub bleibt Control Plane und Eigentümer von Katalog, Routingkonfiguration,
Validierung, Policy, Audit und Task Queue. Worker führen eine vom Hub bereits
aufgelöste Auswahl aus. Ein Worker darf daraus keinen eigenen Router und keine
Worker-zu-Worker-Delegation ableiten.

## Begriffe

| Begriff | Bedeutung | Beispiel |
|---|---|---|
| Provider | API- oder Runtime-Familie, die Inferenz anbietet | `lmstudio`, `ollama`, `openrouter` |
| Executor | konkreter Ausführungsweg, insbesondere eine CLI | Codex CLI, Claude Code, OpenCode |
| Modellidentität | providergebundene technische Modell-ID | `qwen2.5-coder:7b` |
| Alias | weiterer Name derselben beobachteten Identität | lokaler Runtime-Alias |
| Profil | ausführbare, policytragende Konfiguration für ein Modell | `local_coder` |
| Consumer | registrierter Ananta-Aufrufer, der eine Route benötigt | `chat.ai_snake`, `task.planning` |
| Assignment | Zuweisung eines Profils/Modells oder `inherit`/`disabled` zu Consumer und Scope | globales Chat-Profil |
| Fallback-Gruppe | geordnete Kandidaten mit Retry-, Trigger-, Kosten-, Kontext- und Policy-Regeln | `local-first` |
| effektive Route | nach Scope-Präzedenz und allen harten Gates verbleibende Auswahl | lokaler Coder, Cloud blockiert |

Ein registriertes Modell ist nicht automatisch ausführbar. Katalogeintrag,
Profil, Credential-Status, Providerzustand, benötigte Capability und Policy
müssen zusammenpassen.

## Inventarzustände richtig lesen

Die Oberfläche unterscheidet bewusst vier Aussagen:

- **registriert**: Ein Profil oder Adapter kennt die Identität.
- **entdeckt**: Die Quelle hat sie in einem aktuellen Discovery-Lauf geliefert.
- **beobachtet**: Eine Runtime oder ein Remote-Hub hat die Identität gemeldet;
  das ist keine lokale Vertrauens- oder Ausführbarkeitsgarantie.
- **verfügbar**: Die Quelle ist aktuell erreichbar und der Eintrag nicht stale.

`unavailable` bedeutet, dass die Quelle nicht antwortete. `stale` bedeutet,
dass ein letzter erfolgreicher, begrenzt weiterverwendbarer Snapshot angezeigt
wird. `unresolved` bedeutet, dass eine gespeicherte Zuweisung nicht auf eine
bekannte Identität abgebildet werden kann. `capability_mismatch` bedeutet, dass
das Modell existiert, aber eine Pflichtfähigkeit des Consumers nicht erfüllt.

Quellen werden isoliert gesammelt, gecacht und zusammengeführt. Der Ausfall
einer Quelle löscht nicht die Ergebnisse anderer Quellen. Gleichzeitige
Refreshes werden koalesziert. Der Katalog liefert keine API-Keys, Login-Tokens,
Credential-Dateien oder geheime Environment-Werte aus.

## Unterstützte Quellen

### Ollama

Der Adapter liest die lokal bekannten Modelle. Die Auswahl lädt oder startet
kein Modell. Profile müssen Kontextfenster, Toolmodus und lokale Policy korrekt
beschreiben. Ein typisches lokales Profil bleibt bei Secret-Kontext zulässig:

```yaml
profile_id: local_ollama_coder
provider_id: ollama
model: qwen2.5-coder:7b
local: true
cloud: false
cloud_allowed: false
supports_tools: true
tool_calling_mode: prompt_json
```

### LM Studio

Der OpenAI-kompatible Adapter entdeckt die von LM Studio angebotenen Modelle.
Die Base URL gehört in Runtime-Konfiguration, nicht in ein Frontend-Formular.
Ein `No route to host` ist ein Netzwerk-/Runtimeproblem und kein Grund, still
auf `https://api.openai.com/v1` zurückzufallen.

```yaml
profile_id: local_lfm_chat
provider_id: lmstudio
model: lfm2.5-2.6b
local: true
cloud: false
cloud_allowed: false
```

### OpenRouter

OpenRouter wird über einen eigenen Adapter inventarisiert. Cloud-Profile müssen
Cloud-Nutzung explizit erlauben und Secret-Kontext blockieren. Der Read-Model
zeigt höchstens, ob ein Credential konfiguriert ist.

```yaml
profile_id: cloud_reviewer
provider_id: openrouter
model: vendor/model
local: false
cloud: true
cloud_allowed: true
block_secret_context: true
api_key_env: OPENROUTER_API_KEY
```

### Codex CLI, Claude Code und weitere CLIs

CLI-Backends werden über `agent.cli_backends.*` inventarisiert. Codex CLI,
Claude Code, OpenCode, Aider und Mistral Code können je nach Installation nur
eine partielle oder beobachtete Modellmenge melden. Einige CLIs besitzen keine
stabile „list models“-Schnittstelle; dann projiziert der Adapter ausschließlich
nachweisbare konfigurierte, registrierte oder im CLI-Status beobachtete IDs.
Ananta erfindet keine vollständige Modellliste.

CLI-Verfügbarkeit und Modellverfügbarkeit sind getrennt. Eine installierte CLI
beweist weder Loginstatus noch Zugriff auf jede denkbare Modell-ID. Credentials
bleiben im jeweiligen CLI- oder Secret-Store.

### Ananta Worker und Remote-Hubs

Worker- und Remote-Hub-Meldungen sind Trust-Facts: Quelle, Hop-Distanz,
Beobachtungszustand und Vertrauensgrenze bleiben sichtbar. Eine entfernte
Beobachtung erzeugt keine implizite lokale Route und erweitert keine Rechte.

## Consumer und Scope-Präzedenz

Consumer sind in einer Hub-Registry mit stabiler ID, Kategorie,
Pflicht-Capabilities, erlaubten Scopes und Routability registriert. Dadurch
hardcodiert die Angular-Oberfläche keine abschließende Aufgabenliste.

Die engste passende Zuweisung gewinnt:

1. Request-/Runtime-Override, soweit der Aufrufer dazu berechtigt ist
2. Task- oder Workflow-spezifischer Scope
3. Agent-, Team-, Projekt- oder Rollen-Scope
4. globale Consumer-Zuweisung
5. zentraler Default
6. explizite Fallback-Gruppe
7. kompatibler Legacy-Fallback während der Migration

`inherit` setzt keinen leeren Wert, sondern delegiert an den nächstbreiteren
Scope. `disabled` blockiert den Consumer explizit. Ein Assignment auf ein Profil
garantiert noch keine Ausführung: Security, Datenklasse, Secret-Erkennung,
Capabilities, Kontextfenster, Kostenlimit und Providerzustand bleiben harte
Gates.

## Local-first und policy-blocked Cloud

Eine sichere Gruppe kann so gedacht werden:

```text
local_lfm_chat
  -> bei provider_unavailable: local_ollama_coder
  -> bei erlaubtem öffentlichem Kontext: cloud_reviewer
```

Enthält der Kontext Secrets oder eine vertrauliche Datenklasse, wird
`cloud_reviewer` als `policy_blocked` erklärt. `stop_on_policy_block=true`
verhindert, dass ein stärkerer Cloud-Kandidat die Sperre umgeht. Ist kein
lokaler Kandidat kompatibel, liefert der Resolver „keine ausführbare Route“;
er ersetzt die Policy nicht durch eine bequeme Cloud-Auswahl.

Fallback-Kandidaten besitzen optional:

- erlaubte Fehlertrigger,
- Kandidaten- und Gesamt-Retry-Budgets,
- maximales Kontextfenster,
- Kostenobergrenze,
- Tool-/JSON-Anforderungen,
- Cloud-Erlaubnis und Stop-on-policy-block.

## Bedienung der Seite Modelle

Die Seite besteht aus:

- **Katalog**: Suche, Quell-/Status-/Capability-Filter, virtuelle 60er-Fenster
  und Detailansicht.
- **Zuweisungen**: gruppierte Consumer-Matrix, globale und engere Scopes,
  Mehrfachauswahl und ein gemeinsamer durchsuchbarer Profilindex.
- **Fallbacks**: geordnete Kandidaten, Bedingungen und tastaturbedienbare
  Aktionen.
- **Änderungen**: Diff, Hub-Validierung und atomarer Apply mit erwarteter
  Revision.
- **Cognitive Styles**: empirische Style-Scores, Zielbereiche, Confidence,
  Alter und Routingbegründung.
- **Betrieb/Migration**: Source-Zustände, Diagnose, Shadow-Vergleich,
  Release-Gate und explizite Migration.

Bei HTTP 409 hat ein anderer Administrator inzwischen gespeichert. Der lokale
Draft wird nicht blind überschrieben; neu laden, Diff prüfen und erneut
anwenden. Navigation mit Dirty-State fordert eine Entscheidung an.

## Admin-APIs und Capabilities

Alle Endpunkte sind Hub-Endpunkte und authentifiziert.

| API | Zweck | Capability |
|---|---|---|
| `GET /models/catalog/v2` | kanonisches, paginiertes Inventar | `model_catalog.read` |
| `POST /models/catalog/v2/refresh` | isolierten Refresh anstoßen | `model_catalog.refresh` |
| `GET /models/consumers/v1` | Consumer-Registry | `model_routing.read` |
| `GET /models/routing/v1` | Routingkonfiguration | `model_routing.read` |
| `POST /models/routing/v1/validate` | geschlossenen Draft validieren | `model_routing.validate` |
| `PUT /models/routing/v1` | atomarer Compare-and-swap-Apply | `model_routing.mutate` |
| `POST /models/routing/v1/dry-run` | effektive Route erklären | `model_routing.validate` |
| `GET /models/routing/v1/templates` | sichere Vorlagen | `model_routing.read` |
| `POST /models/routing/v1/import` | Import prüfen, nicht ungeprüft anwenden | `model_routing.validate` |
| `GET /models/routing/v1/export` | secretfreier Export | `model_routing.export` |
| `GET /models/routing/v1/diagnostics` | aggregierte Diagnose | `model_routing.read` |
| `GET /models/routing/v1/diagnostics/export` | secretfreier Diagnoseexport | `model_routing.export` |
| `GET /models/routing/v1/migration/preview` | Legacy-Mapping prüfen | `model_routing.read` |
| `POST /models/routing/v1/migration/apply` | Digest- und revisionsgebundene Migration | `model_routing.mutate` |
| `GET /models/routing/v1/migration/shadow` | Alt/Neu-Vergleich | `model_routing.read` |
| `GET /models/routing/v1/release-gate` | dynamische und Test-Gates | `model_routing.read` |

Unbekannte Felder werden abgewiesen. Import und Mutation erlauben keine
Credential-Felder. Der Hub validiert Consumer, Scope, Profil, Modellidentität,
Fallbackgraph und Policy erneut; Frontendvalidierung ist nur frühes Feedback.

## Beobachtbarkeit

Prometheus-Metriken verwenden ausschließlich begrenzte Labels:

- `model_inventory_source_outcomes_total{source_kind,status}`
- `model_routing_decisions_total{outcome}`
- `model_routing_validation_errors_total{severity}`

Diagnose- und Auditdaten enthalten Consumer, Profil, Outcome, Revision und
Reason-Codes, aber keine Prompts, Tokeninhalte oder Credentials. Nutzungsdaten
sind aggregierte Auswahl- und Fallbackzähler.

## Migration und Rollout

Die Flags werden in dieser Reihenfolge aktiviert:

```text
FEATURE_ANGULAR_MODEL_DASHBOARD_ENABLED
  -> FEATURE_MODEL_CATALOG_V2_ENABLED
  -> FEATURE_MODEL_ROUTING_EDITOR_ENABLED
  -> FEATURE_LEGACY_MODEL_PICKER_DEPRECATION_ENABLED
```

1. Dashboard und Katalog read-only aktivieren.
2. Legacy-Migration als Vorschau ausführen; `unresolved`, `ambiguous` und
   Shadow-Differenzen beseitigen.
3. Release-Evidenz erzeugen:

   ```bash
   scripts/model_routing_release_gate.py
   ```

   Das Gate führt Contract-, Security-, Store-, Browser- und Performance-Tests
   aus und schreibt nur bei vollständigem Erfolg
   `artifacts/test-gates/model-routing-release-gate.json`. Digests binden die
   Evidenz an alle relevanten Quelldateien; Source-Drift sperrt das Gate.
4. Routing-Editor aktivieren. Der Config-Endpunkt verweigert die Aktivierung,
   solange dynamische Migration oder Testevidenz fehlschlagen.
5. Nach stabiler Shadow-Evidenz Legacy-Picker deprecaten. Alte Deep-Links führen
   weiterhin auf die kanonische Modelle-Seite.

Nach bestandenem Release-Gate sind die Modelle-Navigation und Catalog-v2-Reads
kanonisch und nicht mehr von den früheren Dashboard-/Catalog-Flags abhängig.
Die alten Schlüssel bleiben während der kompatiblen Migration lesbar. Nur der
mutierende Routing-Editor und die Legacy-Picker-Deprecation bleiben getrennte,
fail-closed Rollout-/Rollback-Schalter.

Optional kann der Evidenzpfad gesetzt werden:

```bash
MODEL_ROUTING_RELEASE_EVIDENCE_PATH=/app/artifacts/test-gates/model-routing-release-gate.json
```

## Rollback

Bei Regression:

1. `FEATURE_MODEL_ROUTING_EDITOR_ENABLED=0` setzen. Das macht die zentrale
   Konfiguration read-only und löscht sie nicht.
2. Falls nötig `FEATURE_LEGACY_MODEL_PICKER_DEPRECATION_ENABLED=0` setzen, um
   den vorherigen UI-Pfad wieder anzubieten.
3. Katalog v2 kann unabhängig deaktiviert werden; gespeicherte Assignments und
   Fallbacks bleiben erhalten.
4. Letzte exportierte Routingkonfiguration prüfen und mit ihrer erwarteten
   Revision atomar zurückspielen. Kein DB- oder Datei-Reset ist erforderlich.
5. Shadow-, Diagnose- und Release-Gate erneut ausführen, bevor Mutation wieder
   freigeschaltet wird.

## Troubleshooting

| Symptom | Prüfung | Korrektur |
|---|---|---|
| Quelle `unavailable` | Source-Zustand und Providerprozess | Netzwerk/Base URL/Runtime reparieren, dann Refresh |
| Quelle `stale` | Alter und letzter Fehler | nicht als aktuelle Verfügbarkeit behandeln; Refresh |
| Assignment `unresolved` | Profil-ID und Inventaridentität | Profil registrieren oder Assignment explizit ändern |
| `capability_mismatch` | Consumer-Pflichten vs. Profildetails | kompatibles Profil wählen; Capability nicht vortäuschen |
| Cloud `policy_blocked` | Datenklasse, Secret-Kontext, Cloud-Flag | lokalen Kandidaten bereitstellen; Policy nicht umgehen |
| 409 beim Speichern | Serverrevision | neu laden, Diff neu bewerten, erneut atomar anwenden |
| Release-Gate `source_drift` | geänderte Quelldateien | vollständiges Gate erneut laufen lassen und Evidenz committen |
| CLI zeigt wenige Modelle | CLI-Discovery-Modus | registrierte/observed IDs verwenden; keine Liste erfinden |

## Sicherheits- und SOLID-Grenzen

Inventaradapter implementieren kleine Quellenports; Aggregation, Routing,
Migration, Transfer, Validation, Observability und Release-Evidenz sind getrennte
Services (SRP/DIP). Neue Provider werden als Adapter ergänzt, nicht durch neue
Routingzweige im Frontend (OCP). Alle Adapter liefern denselben kanonischen
Vertrag und müssen ohne versteckte Nebenwirkungen substituierbar bleiben (LSP).

Die weiterhin breite Angular-`ModelDashboardStore` ist ein bewusst erhaltener
technischer Schuldenpunkt: Sie koordiniert mehrere bereits getrennte API-Clients
und View-Drafts. Migration besitzt bereits einen eigenen Store; weitere
Aufteilung sollte inkrementell entlang Katalog, Assignments und Style erfolgen,
ohne den öffentlichen UI-Vertrag oder Taskfluss zu brechen.
