# Pi als Coding-Agent-Provider (PI-T05/PI-T06)

Pi ist ein optionaler, standardmäßig **deaktivierter** Coding-Agent-Provider hinter
`CodingAgentProvider` (`agent/cli_backends/pi_provider.py`). Er läuft **ohne Tools** und mit einer
eingefrorenen, auftragsbezogenen Konfiguration. Der Hub bleibt alleiniger Eigentümer von Task Queue,
Routing, Modellwahl, Budget, Delegation und Berechtigungen; Pi orchestriert keine weiteren Worker.

Klassifizierung: `IntegrationKind.CLI`, `FreeClass.OPEN_SOURCE_BYOK`; Inferenzkosten werden separat
ausgewiesen, Pi selbst ist der Client. Siehe auch `docs/coding-agent-providers.md`.

## Voraussetzungen

- Gepinnte Pi-SDK (`PI_VERSION = 0.85.1`) im Worker, aufgelöst über `pi_sdk_command` aus einem
  **absoluten** Binary-Pfad; fehlt Node oder die gepinnte SDK, schlägt der Start typisiert fehl
  (`node_runtime_unavailable`) statt still zu laufen.
- Ein explizit ausgewähltes Inferenz-Ziel (`CodingAgentInferenceTarget`) mit `client_id = "pi"` und
  `provider_id` aus `{ollama, lmstudio, openrouter}` – validiert durch `validate_pi_target`:
  Modell-ID nicht leer/`-`-führend; `base_url` ohne Credentials/Query/Fragment; `openrouter` nur über
  HTTPS; API-Key nur auftragsbezogen.

## Einrichtung

1. Provider aktivieren (`enabled=True`) und Ziel setzen. Ohne Aktivierung liest Pi keine Secrets und
   verhält sich Ananta exakt wie ohne Pi.
2. System- und Zusatzprompt werden an eine **eigene unveränderliche auftragsbezogene Datei** gebunden;
   `--no-context-files` allein verhindert die globale `SYSTEM.md`-Ladung nicht. Es wird keine
   Dateiauflösung aus Prompttext und keine globale Benutzerkonfiguration übernommen.
3. `max_tokens` ist auf `1..1024` begrenzt; der Hub-Budgetdeckel kappt jeden Aufruf zusätzlich.
4. Projekt-/Benutzer-Konfiguration, `AGENTS.md`, Skills und Extensions sind **Eingaben ohne zusätzliche
   Autorität**. Automatische fremde Extension-Ladung/-Installation und Selbständerung außerhalb des
   Auftrags sind unzulässig.

## Grenzen

- **No-Tools-Profil:** Pi erhält keinen Datei-, Netzwerk- oder Toolzugriff. Migrationen starten in einem
  eigenen **leeren Laufverzeichnis**, nicht im Projekt-`cwd`; der tatsächliche Session-`cwd` wird getrennt
  vom Hub-Auftrags-/Repositorybezug validiert. Direkte Datei-Tools bleiben ohne technische Sandbox
  `unsupported`.
- Read-only ist rein technisch erzwungen; fehlende Pi-Berechtigungsdialoge gelten niemals als Freigabe.
- Jeder Worker bleibt in eigenem Container mit auftragsgebundenem Workspace; fehlende Autorisierung
  liefert ein **begrenztes maschinenlesbares** `blocked`/`failed` (kein unbegrenztes Warten, kein Bypass).

## CodeCompass-Ablauf

CodeCompass-Kontext wird Pi **nur** aus einem freigegebenen `ContextBundle` zugeführt; die bestehende
Tool-/MCP-Brücke wird wiederverwendet, soweit Pi sie sicher unterstützt, sonst wird die Capability als
`unsupported` ausgewiesen. Pi löst selbst keinen Graph-/Indexzugriff aus und erhält keine
Hub-CodeCompass-Befugnisse (die gehören dem Hub).

## Evidenz und Ergebnisbindung

- Der **Hub reserviert die RUN-Identität vor Ausführung** und registriert Quellen
  (`pi_run_evidence_reservation`, `pi_runtime_manifest_admission`). Pi übernimmt ausschließlich
  zugewiesene Identitäten und kann **keine Evidenz selbst ausstellen**.
- Das Manifest wird gegen exakte Fakten (Tenant, Projekt, Worker-ID/-URL) vor der SRC-Aufnahme geprüft;
  falsche Origin-/Content-/Policy-/Scope-Bindungen und geänderte Deployments werden abgewiesen.
- Pi-Ereignisse werden auf bestehende Provider-Events abgebildet; Teilresultate, Fehler und
  widersprüchliche Abschlussereignisse werden deterministisch behandelt (`pi_events`,
  `parse_pi_one_shot`).
- Artefakte und Resultate laufen über den vorhandenen verifizierten Hub-Ingress; Wiederholung ist
  idempotent, abgelaufene Leases und verspätete Resultate werden abgelehnt.
- Resume/Sitzungszweige sind strikt an Tenant, Projekt, Task, Assignment und Dispatch-Lease gebunden;
  keine automatische öffentliche Freigabe oder Gist-Uploads.

## Transportentscheidung

Gewählt ist der **schmalste ausreichende Transport**: der Pi-SDK-HTTP-Client, begrenzt auf **einen**
freigegebenen `POST <base>/chat/completions`. Die Erzwingung liegt am SDK-Rand
(`pi_http_transport.mjs`):

- genau ein Versuch (SDK-Retries können kein zweites Mal ausgeben);
- nur string/URL-Input, `POST`, JSON-Body ≤ 2 MB, `body.model == modelId`, `stream: true`,
  `max_tokens == maxTokens`, **keine** Tools/Functions/tool_choice außer `none`, kein `models`/`route`;
- **Redirects werden vor Kontakt zum Ziel abgelehnt** (`redirect: "error"`, zusätzlich Status-3xx- und
  URL-Prüfung); für `openrouter` ist striktes Routing-Policy-Objekt Pflicht;
- TLS für externe Endpunkte, lokale Provider über HTTP.

RPC/CLI-Transport wurden nach Integrationsaufwand verglichen; der SDK-HTTP-Weg ist der schmalste, der
strukturierte Ausgabe und harte Endpunktbindung zugleich erlaubt.

## Abschalten und Rollback

Pi ist standardmäßig aus. Abschalten heißt `enabled=False` setzen – es werden keine Secrets gelesen, kein
Prozess gestartet und keine Konfiguration verändert. Pi-Startmigrationen dürfen **kein Benutzerprojekt
verändern**; es gibt keinen globalen Zustand, der zurückgerollt werden müsste. Bestehende Provider
(`sgpt`, `opencode`, `codex`, `aider`, `mistral`, `claude`) bleiben unverändert nutzbar.

## Tests

Headless mit injizierten Prozess-/Transport-Ports: Erfolg, kaputte JSON-Events, Timeout, Abbruch,
Output-Overflow, Secret-Redaktion; negative Fälle (Schreibversuch im Read-only-Modus, fremde Session,
unerlaubte Extension, Modellwechsel, stale Lease) ohne menschliche Eingabe. Ein optionaler Live-Smoke
für lokalen und externen Endpunkt ist **nur** mit explizitem Opt-in aktiv und schlägt ohne Auth/Binary
begrenzt fehl; fehlende Gewichte oder Hardware werden nie als bestanden vorgetäuscht. Synthetische Läufe
sind technische Beobachtungen und erfüllen kein Produktionsfreigabe-Gate.
