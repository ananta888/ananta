# Pi: Quellabgleich und Integrationsentscheidung

## Stand und belegte Schnittstellen

Quellabgleich vom 2026-09-09 gegen Ananta `921c7f306` und den anonym
aufgelösten Pi-Commit `05c6229813414010445558db9a80c84e15d65e70`.
Die offizielle Website verweist inzwischen auf `earendil-works/pi`.
Paket-Metadaten dieses exakten Commits und npm bestätigen
`@earendil-works/pi-coding-agent` **0.85.1**, Node **>=22.19.0** und MIT.
Die Lizenzdatei enthält den Copyright-Hinweis von Mario Zechner; bei der
Bereitstellung müssen die mitgelieferten Hinweise erhalten bleiben.
Ein erfolgreicher Metadatenabruf ist noch keine installierte/verifizierte
Worker-Laufzeit. [Website](https://pi.dev/),
[gepinntes Paket](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/package.json),
[Lizenz](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/LICENSE).

Das npm-Register nennt für diese Version die Integrität
`sha512-FGRN+OHbWaefBPGaTggAdLjrIHW+s2PzLyglz/5dfLzb9of7uuXMXYC0fJIeZTw+shS32o2cuQ9jF7YSDuL/oQ==`.
Dies ist die gelesene Registerangabe, noch kein selbst geprüfter Tarball-Hash.
[Versionsmetadaten](https://registry.npmjs.org/@earendil-works/pi-coding-agent/0.85.1).

| Transport | Passung zum bestehenden Ananta-Port | Entscheidung |
| --- | --- | --- |
| Print/JSON | Ein begrenzter Prozess, Prompt über stdin, JSONL-Ausgabe | Kleinster ausreichender Einstieg für einen delegierten Auftrag |
| RPC | Bidirektionale Kommandos, asynchrone Antworten und Sitzungsaustausch | Erst bei belegtem Bedarf; zusätzliche Framing-/Lebenszyklusverantwortung |
| SDK | Direkte Session-/ResourceLoader-/Tool-Ports, aber eigener Node-Adapter | Alternative, falls CLI-Eingabe-/Toolgrenzen technisch nicht ausreichen |

Eine RPC-Promptbestätigung bedeutet Annahme, nicht erfolgreichen Abschluss.
JSON-Ereignisse unterscheiden ebenfalls Streaming von abschließenden
Nachrichten. Ein Adapter muss widersprüchliche/fehlende Endereignisse und
Prozessfehler auswerten, statt Exit-Code oder ein einzelnes Event als
vollständige Aufgabenabnahme zu behandeln.
[JSON-Vertrag](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/json.md),
[RPC](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/rpc.md),
[SDK](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/sdk.md).

## Sicherheits- und Modellgrenzen

Pi besitzt keine eingebaute Sandbox. Projektvertrauen steuert das Laden von
Konfiguration und Erweiterungen, nicht die späteren Datei-/Toolrechte.
Nichtinteraktive Modi fragen nicht nach Projektvertrauen. `--no-approve`
verweigert diese Projektressourcen; globale oder explizite Erweiterungen sind
damit allein nicht ausgeschlossen. AGENTS-/CLAUDE-Kontext wird unabhängig von
Projektvertrauen geladen, sofern die Erkennung nicht ausdrücklich deaktiviert
ist. Das sind Eingabegrenzen, keine Hub-Autorisierung.
[Sicherheitsmodell](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/security.md).

Der spätere Adapter benötigt einen auftragsisolierten Konfigurationsbereich,
explizit deaktivierte automatische Extensions/Skills/Templates/Themes und
Kontextdateierkennung sowie unveränderlich bereitgestellte Laufzeitdateien.
`PI_OFFLINE` unterbindet Start-Netzwerkoperationen; es ist keine Netzwerk-Firewall
für Tool- oder Inferenzaufrufe. Der tatsächliche Worker-Container muss Datei-
und Netzgrenzen durchsetzen. Keine Host-Credentials oder globalen Pi-Sitzungen
einbinden, keine Installations-/Updatebefehle aus Modelltext ableiten.
Read-only ist erst nach einem technischen Negativtest verfügbar; andernfalls
ist die Fähigkeit ausdrücklich unsupported.
[CLI-Optionen](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/usage.md),
[Umgebung](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/environment-variables.md),
[Containergrenze](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/containerization.md).

OpenRouter unterstützt `OPENROUTER_API_KEY`; Ollama und LM Studio werden über
explizite Provider-/Modellkonfiguration angebunden. Vorhandene fremde
`auth.json`-Dateien sind keine erlaubte Credential-Quelle: ihr Schlüsselformat
kann sogar Kommandos enthalten. Ananta projiziert nur das freigegebene
Inferenzziel und auftragsbezogene Credentials. Kostenklasse des Clients bleibt
`open_source_byok`, Inferenzkosten bleiben separat. Weder automatische
Modellwechsel noch ein OAuth-Login werden als Headless-Fallback aktiviert.
[Provider](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/providers.md),
[lokale Modelle](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/models.md).

## Wiederverwendung und Überschneidungen

`agent/cli_backends/coding_agent_contract.py` definiert Provider, Fähigkeiten,
Events und `ProcessRunnerPort`; `coding_agent_process.py` implementiert den
gemeinsamen Prozesslebenszyklus. `coding_agent_profiles.py` kombiniert die
bestehenden deklarativen CLI-Profile mit Erkennung und erlaubter Umgebung.
`coding_agent_targets.py` trennt Client und Inferenzziel; Auswahl und Policy
bleiben beim Hub. Ein Pi-spezifischer Adapter darf die bestehenden Clients
nicht durch neue globale Konfigurationsannahmen verändern.

Der offene Category-Plan `todos/todo.omp-runtime-evaluation.json` ist kein
bereits implementierter Pi-Provider. Gemeinsame Prozesshärtung und
Modell-/Event-Ports sollen beiden Vorhaben dienen. OMP-spezifische Subagenten,
Advisor, persistente Kernel, Memory und ACP sind getrennte Evaluationspunkte,
keine stillschweigend mit Pi aktivierten Fähigkeiten. CodeCompass liefert nur
freigegebenen ContextBundle-Inhalt; MCP wird ohne abgesicherten Adapter nicht
als unterstützt ausgewiesen. Keine zweite Task Queue oder Worker-Delegation.

## Gefundene Prozess-Voraussetzungen

Der Quellstand des gemeinsamen Prozessadapters erfüllt seinen behaupteten
begrenzten Lebenszyklus noch nicht in allen Fällen:

1. Er schreibt den vollständigen Prompt synchron, bevor Ausgabeleser und
   Fristüberwachung laufen. Ein Kind, das stdin nicht liest, kann den Aufruf
   bereits dort blockieren.
2. `readline()` liest ohne Größenlimit. Eine lange Ausgabe ohne Zeilenumbruch
   wird vor der eigentlichen Ausgabelimitprüfung gepuffert.
3. Cleanup überspringt Prozessgruppen bei bereits beendetem Hauptprozess.
   Auch ein nach TERM beendeter Hauptprozess beweist nicht, dass seine
   TERM-ignorierenden Kinder beendet sind.

Diese Feststellungen sind zunächst Quellbefunde, noch keine ausgeführten
Fehlerreproduktionen. Vor Pi-Ausführung: kurze headless Regressionen mit
eigenen begrenzten Kindern, dann Korrektur am vorhandenen Prozessport.
Beibehalten werden Shellfreiheit, unveränderte Provider-Schnittstellen,
begrenzte UTF-8-/Event-Verarbeitung, Secret-Redaktion und bestehende Ergebnis-
Codes. Prozesssteuerung gehört nicht in Pi-Modellauflösung oder Hub-Policy
(SRP/DIP); zusätzliche I/O-/Cleanup-Helfer bleiben eng und testbar.

## Entscheidung und offener Nachweis

Go für einen standardmäßig deaktivierten, isolierten CLI/JSON-Prototyp nach
Behebung der gemeinsamen Prozessgrenzen. Kein Go für unbeschränkte Host-
Ausführung, automatische fremde Erweiterungen oder eine Pi-Control-Plane.
Die Bereitstellung, echten CLI-Negativtests, Modellanbindung, Event-/Lease-
Bindung und optionalen lokalen/externen Modellläufe sind noch offen.
Zum Zeitpunkt dieses Quellabgleichs blieb PI-T01 bis zur technischen
Eignungsprüfung in Bearbeitung; deren späteres Ergebnis folgt unten.
Der Quellabgleich schließt weder den Pi-Track noch die laufende Meet-Abnahme.
Alle bisherigen Abfragen sind technische Beobachtungen ohne nachträglich
vergebene SRC-/RUN-Identitäten.

## Verifizierte gemeinsame Prozesshärtung

Die vier gezielten Laufzeitfälle reproduzierten die drei Quellbefunde in
13.78 Sekunden: stdin blockierte die 150-ms-Frist bis etwa 1.53 Sekunden,
überlange Ausgabe wurde erst am Zeilen-/Prozessende erkannt, und eigene Kinder
überlebten sowohl bereits beendete als auch auf TERM endende Hauptprozesse.
Ein früherer Versuch hatte zusätzlich einen zu kurzen äußeren Timeout für die
Flask-Testvorbereitung; das war ein Testaufbaufehler, kein vierter Produktfehler.
Die unveränderten inneren Frist-/Laufzeitassertionen prüfen weiterhin den Fehler.

`coding_agent_process_io.py` übernimmt begrenzte 4096-Zeichen-Leseoperationen,
den nebenläufigen Prompt-Writer und das Aufräumen der eigenen POSIX-Gruppe
einschließlich KILL nach Hauptprozessende. `coding_agent_process_output.py`
setzt erst vollständige LF-Zeilen zu Events zusammen, redigiert auch über
Lesestückgrenzen verteilte Secrets und begrenzt rohe wie redigierte Ausgabe.
Der vorhandene Prozessport behält Frist, Abbruch und Ergebnisentscheidung.
Pipe-Fehler/unvollständiges Nachlesen ergeben `process_io_failed`, nicht Erfolg.
Ein gestoppter Prozess erhält höchstens eine weitere Sekunde für Pipe-Drain;
dies erlaubt keine neue Ausführung. Cleanup kommt zur Ausführungsfrist hinzu.
Thread-Startfehler räumen ebenfalls
den eigenen Prozess und geöffnete Pipes auf.

Alle 35 neuen und bestehenden Prozess-, OpenCode-Adapter- und CLI-Profiltests
bestanden in 31.34 Sekunden. Die vorherigen 25/31 grünen Tests sind überlappende
Zwischenstände, keine zusätzlichen unabhängigen Fälle. Ruff und der
CLI-Namespace-Detektor bestehen. Kein Modell, Browser, GPU oder Netzwerk war
für diese Regressionen erforderlich. Dies schließt die genannten gemeinsamen
Prozessdefekte, nicht den Pi-Provider oder dessen Container-/Tool-Sicherheitsgate.
Prozessgruppen-Cleanup ist keine Sandbox gegen absichtlich aus der Gruppe
ausbrechende Prozesse; diese Grenze bleibt Aufgabe des Worker-Containers.

## Reale Paketprüfung und endgültiger Transportentscheid

Version 0.85.1 wurde anschließend ausschließlich in einem eigenen
Runtime-Verzeichnis mit deaktivierten npm-Installationsskripten installiert.
Der erzeugte Lock enthält die oben dokumentierte Integrität. Das echte CLI
meldet 0.85.1 unter Node 24.13.0 in einem eigenen nicht privilegierten,
read-only Container ohne externes Netzwerk, GPU oder Host-Credentials.
Der vorhandene unveränderliche Test-Image-Stand `46b062604e55` lieferte hierfür
nur die Node-Laufzeit; das ist noch kein dediziertes produktives Pi-Worker-Image.

Ein ausschließlich containerlokaler HTTP-Modellstub beantwortete genau einen
CLI/JSON-Aufruf. Projekt-/Eltern-/globale AGENTS-Dateien, globale und lokale
Erweiterungen sowie Systemprompt-Dateien enthielten absichtlich fremde
Testmarker. Der erste Aufruf endete technisch erfolgreich, aber das
Kontext-Isolationskriterium schlug fehl: Die globale `SYSTEM.md` wurde trotz
`--no-context-files` geladen. Es wurde keine fremde Erweiterung ausgeführt.
Der installierte `resource-loader.js` bestätigt die getrennte Systemprompt-
Erkennung; dies ist eine notwendige Adaptergrenze, kein behaupteter Pi-Bug.

Die Wiederholung verwendete zusätzlich explizite eigene System- und
Zusatzprompt-Dateien, darunter eine leere Zusatzdatei. Sie bestand: Exit 0,
genau eine Anfrage mit dem ausgewählten Modell, ein `agent_end` und ein
nachfolgendes `agent_settled`, keine fremden Kontextmarker, keine ausgeführte
Erweiterung und kein stderr. Auch absichtlich vorhandene `APPEND_SYSTEM.md`
blieben außen vor. Innere 20-s- und äußere 35-s-Fristen waren aktiv; sämtliche
Testcontainer wurden beendet und entfernt. Die Antwort war synthetisch,
kein echtes LLM-Ergebnis und keine Hub-registrierte Release-Evidenz.

Damit ist PI-T01 abgeschlossen: Go für den optionalen CLI/JSON-Adapter über
den bestehenden Prozessport. Der Adapter muss beide Prompt-Quellen explizit
an eigene unveränderliche Dateien binden. Freie Prompttexte als Dateiselektor
sind ungeeignet: Pi liest einen gleichnamigen vorhandenen Pfad bevorzugt.
Modelle, Settings, Auth und Erweiterungsladung bleiben geschlossen; ein
Read-only-Toolprofil oder Resume wird erst nach eigener technischer Abnahme
angeboten. PI-T02 bis PI-T06 bleiben offen; der erfolgreiche No-Tools-Prototyp
ist keine vollständige Coding-, Datei-Sandbox- oder Provider-Integration.

## Optionale Worker-Bereitstellung

Der bestehende administrative Bereitstellungspfad unterstützt nun `pi`:
`POST /api/sgpt/backends/pi/provision`, Aktion `status` oder `install`.
Am Hub muss ein registrierter Worker gewählt werden. Weitergeleitet wird nur
die Aktion; Paketname und Version aus einem Request werden nicht übernommen.
Die Installation allein aktiviert weder einen Provider noch Modell-Routing.
Pi erhält keinen interaktiven Account-Login-/Worker-Action-Pfad.

Der feste Katalogeintrag installiert ausschließlich
`@earendil-works/pi-coding-agent@0.85.1` im vorhandenen Worker-eigenen,
versionierten CLI-Verzeichnis. Der vorhandene Worker-Dockerfile pinnt bereits
Node 24.18.0; trotzdem wird die tatsächlich verfügbare Node-Version vor
Installation und Versionsprobe gegen >=22.19.0 geprüft. Erst die exakte
Pi-Ausgabe 0.85.1 ohne Fehlerausgabe ergibt den Bereitstellungsstatus `ready`.
Dieser Status ist keine Bestätigung der noch offenen Provider-Fähigkeiten.

Der neue schmale `HeadlessNodeProvisioning`-Adapter verwendet den injizierbaren
gemeinsamen Prozessport, eine 540-s-Installationsfrist, 5-s-Versionsproben und
Ausgabelimits von 32.768 Zeichen. npm-Lifecycle-Skripte sind deaktiviert. Hub-/Provider-
Credentials, Node-Startoptionen und fremde npm-Konfiguration werden nicht aus
der Umgebung übernommen. Zwei eigene leere Konfigurationsdateien werden nach
dem Aufruf entfernt. Ein technischer npm-Versionsprobeaufruf bestätigte die
Verdrahtung ohne Paketinstallation oder Netzwerk: Node 22.22.1 und npm 9.2.0,
jeweils Exit 0 ohne stderr. Beide Konfigurationstypen dürfen nicht denselben
`/dev/null`-Pfad verwenden; npm lehnt dessen doppelte Ladung ab.

36 fokussierte Pi-/bestehende Provisionierungs- und API-Tests bestehen in
32.09 Sekunden. Abgedeckt sind feste Argumente, temporäre Konfiguration,
fehlendes/ungeeignetes Node, Versionsabweichung, Installationsfehler inklusive
Timeout-/Overflow-/Abbruchcodes, geschlossene Prozessdiagnosen, registrierte
Worker-Weiterleitung und die Ablehnung interaktiver Pi-Login-Routen. Ruff und
der CLI-Namespace-Detektor bestehen. Kein produktiver Worker wurde installiert
oder umgeschaltet; dies sind technische Tests, keine Release-Evidenz.

SRP/DIP: Katalog, Pfade und bestehende Sperren verbleiben im Provisioner;
begrenzte Node-/npm-Ausführung liegt hinter dem vorhandenen Prozessport.
Beibehaltene technische Schuld: Die älteren Installationszweige anderer
CLI-Clients verwenden weiterhin ihren bisherigen `subprocess.run`-Adapter;
deren Ausgabegrenze wird durch diese additive Pi-Änderung nicht verbessert.
PI-T02 ist damit teilweise implementiert. Der eigentliche Provider sowie
Auftragsisolierung, Modell-/Kontextanbindung und Ergebnisbindung bleiben offen.
