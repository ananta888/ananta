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

## Reale Paketprüfung und erster Transportentscheid

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

## Geschlossener JSON-Abschluss und Start-Migrationen

`pi_events.py` prüft den gepinnten Ein-Turn-/No-Tools-Vertrag separat von
Prozesssteuerung, Modellwahl und Hub-Identitäten (SRP). Nur eine vollständige
Folge bis `agent_settled` liefert den autoritativen Antworttext. Modell,
Provider, API und tatsächliches CLI-Arbeitsverzeichnis müssen passen.
`message_end`, `turn_end`, `agent_end` und ein gegebenenfalls vorhandenes
Streaming-`done` dürfen sich nicht widersprechen. Fehlende/doppelte Enden,
Retry, Tool-Aufrufe, Fehler/Abbruch/Längenlimit, ungültiges JSON einschließlich
doppelter Schlüssel und nicht endlicher Konstanten werden geschlossen
abgelehnt. Teiltext wird dabei nicht in einen erfolgreichen Abschluss umgedeutet.

46 deterministische Parser-Prüfungen bestehen in 35.64 Sekunden; Ruff und
Namespace-Detektor bestehen ebenfalls. Zusätzlich akzeptierte der neue Parser
den tatsächlichen 0.85.1-JSON-Stream im isolierten Container mit genau einer
synthetischen Modellantwort. Diese Prüfung verwendet keine externe Inferenz.
Der Parser allein bindet noch keine Hub-Lease und aktiviert keinen Provider.

Ein zusätzlicher realer Negativversuch bestätigte einen Start-Nebeneffekt:
Pi benannte im eigenen Testprojekt `.pi/commands` in `.pi/prompts` um,
obwohl `--no-tools`, `--no-approve` und alle Ressourcen-Abschaltflags gesetzt
waren; der Prozess endete mit Exit 0. Der installierte `migrations.js` und
`main.js` erklären den Aufruf vor der Runtime-Erzeugung. Das ist kein Nachweis
einer Tool-Freigabe, sondern eine zusätzliche Isolationsanforderung.

Der erste No-Tools-Adapter muss deshalb in einem eigenen leeren Laufverzeichnis
starten und erhält freigegebenen CodeCompass-Kontext nur als Eingabe. Das
Projektverzeichnis darf dafür nicht als CLI-cwd verwendet werden. Direkte
Datei-Tools bleiben bis zu einer nachgewiesenen technischen Sandbox unsupported.
Der Session-Header bindet in diesem Profil das eigene Laufverzeichnis, nicht
eine erfundene Repository- oder Evidenzidentität. Alle Teständerungen betrafen
nur eigene Wegwerfdateien; keine Benutzer-Projekte wurden migriert.

## Versionsprobe und Container-Benutzeridentität

Auch die Pi-Versionsprobe verwendet jetzt ein eigenes temporäres
`PI_CODING_AGENT_DIR`, das anschließend entfernt wird. So hängt die Probe
nicht von globaler Pi-Konfiguration ab. 36 Provisionierungs-/API-Regressionen
bestehen nach dieser Ergänzung in 31.83 Sekunden.

Ein Test unter einer bloßen numerischen UID ohne passwd-Eintrag scheiterte
bereits beim Pi-Import: dessen Pfadnormalisierung ruft `os.homedir()` auch bei
einem absoluten Konfigurationspfad auf. Das war eine fehlende Voraussetzung im
isolierten Testimage, keine fehlende Node-Installation. Der reguläre
`Dockerfile.quickstart-no-ollama` legt bereits einen passenden nicht
privilegierten `ananta`-Benutzer an. Mit einem vorhandenen nicht privilegierten
Container-Benutzer bestanden echte Node-/Pi-Versionsprobe und der folgende
isolierte Aufruf ohne HOME-Umschreibung oder fremde Credentials. Ein extern
abgewandeltes Image ohne auflösbare Benutzeridentität bleibt nicht bereit.

## Revidierter Transportentscheid: begrenzter SDK-Prozess

Der tatsächliche No-Tools-CLI-Negativlauf lieferte ein wichtiges Gegenbeispiel:
Nach einem unerwarteten Modell-Toolcall führte Pi zwar keine Dateioperation
aus, startete aber eine zweite Modellanfrage. Der spätere JSON-Parser lehnte
das Ergebnis korrekt ab, konnte diese bereits erfolgte zusätzliche Anfrage
jedoch nicht verhindern. Die CLI ist deshalb nicht mehr der ausreichende
Transport für das Ein-Turn-Profil. RPC löst diese Ausführungsgrenze ebenfalls
nicht durch einen anderen Nachrichtenkanal.

`pi_sdk_entry.mjs` verwendet stattdessen das öffentliche SDK desselben
gepinnten Pakets in einem weiterhin begrenzten Node-Prozess. Es lädt nur den
expliziten Modell-/Konfigurationsbereich, deaktiviert Ressourcen, Tools,
Compaction und Retries, verwendet eine In-Memory-Sitzung und beendet nach einem
Turn. Zusätzlich verweigert der Stream-Adapter jede zweite Modellanfrage oder
Modell-/Provider-/Tool-Abweichung vor deren Ausführung. Die Ausgabe bleibt
kompatibel zum geschlossenen Pi-JSON-Parser; kumulative Streaming-Snapshots
werden nicht wiederholt gepuffert. Dies ist Ausführung eines Hub-Auftrags,
keine zusätzliche Task Queue oder Worker-Orchestrierung.

Der reale synthetische Negativlauf besteht mit diesem Adapter: genau eine
Modellanfrage, `pi_tool_execution_denied`, kein Schreiben, keine Migration
und vollständiges Aufräumen. Auch der normale synthetische Aufruf besteht mit
genau einer Anfrage und dem erwarteten Antworttext. Eine weitere Wiederholung
prüfte die tatsächlich übermittelte Obergrenze von 1024 Ausgabetokens.
8192 Kontext-/1024 Ausgabetokens sind konservative Grenzen dieses Profils,
keine verifizierten Kapazitätsangaben eines realen Modells. Echte lokale und
externe Inferenz sowie die Hub-seitige Konfigurationsprojektion bleiben offen.

16 deterministische Layout-/Target-Prüfungen decken den gepinnten SDK-Nachbarn,
absolutes Node, geänderte Paketidentität, externe SDK-Symlinks und abgelehnte
Endpoint-/Modell-/Credential-Formate ab. Der API-Key wird ausschließlich als
`$ANANTA_PI_API_KEY` mit explizitem `authHeader` projiziert; ein bloßer
Großbuchstabenname wäre in Pi ein Literal. Im Test wird der tatsächliche
Authorization-Header geprüft, ohne ihn zu protokollieren. SDK-Adapter und
Runtime-/Konfigurationsprojektion bleiben separate, kleine Verantwortungen.

## Registrierter, standardmäßig deaktivierter Provider

`PiCodingAgentProvider` implementiert jetzt den vorhandenen Provider-Port und
ist über die bestehende Factory sowie den Capability-Katalog auffindbar.
Er wird nicht in automatisches CLI-Routing oder eine Fallback-Liste aufgenommen.
Ohne explizites Einschalten, ausgewählten Target-Vertrag und injizierte
Auftragsautorisierung findet kein Modellaufruf statt. Autorität wird vor der
Vorbereitung, unmittelbar vor dem Prozess und vor der Ergebnisübernahme
geprüft. Die produktive Bindung dieses Prüfports an Hub-Assignment/Dispatch
Lease ist weiterhin Aufgabe von PI-T03/PI-T05; ein beliebiges `True` aus einer
Testfixture ist keine produktive Freigabe.

Das erste Profil verarbeitet ausschließlich mitgegebenen Kontext und liefert
Text beziehungsweise vorgeschlagene Änderungen. Es akzeptiert nur
`read_only` ohne Session-ID; direkte Schreib-/Shell-Tools, Resume, MCP,
Streaming-Freigabe und OS-Sandbox werden nicht als verfügbar ausgewiesen.
Insbesondere schützen eigene temporäre Verzeichnisse allein nicht vor
anderen absichtlich bösartigen Prozessen derselben UID. Die technische
Container-/Auftragsgrenze muss vor produktiver Aktivierung geprüft werden.

Der Prozessport begrenzt Ausführung, Ausgabe und Cleanup. Erst ein vollständig
validiertes Ergebnis erreicht den Event-Sink; Prozess-/Protokollfehler und
entzogene Autorisierung liefern keinen Teiltext. Nach dem JSON-Dekodieren
folgt eine weitere Secret-Redaktion, damit JSON-Escapes oder kurze Credentials
nicht die zeilenbasierte Prozessredaktion umgehen. Es gibt keine eigene
Evidenzvergabe, Sitzungsveröffentlichung oder Worker-Delegation.

Die letzte fokussierte Provider-/Parser-/bestehende CLI-Regression besteht
mit 91 Tests in 64.89 Sekunden, einschließlich vier zusätzlicher
Secret-Dekodierfälle. Die zuvor bestandenen 103 Tests (72.20 Sekunden)
enthielten außerdem 16 Runtime-/Target-Fälle und überlappen größtenteils;
sie werden nicht zu 194 unabhängigen Tests addiert. Die 36 separaten
Provisionierungs-/API-Prüfungen bestehen ebenfalls. Ruff, Node-Syntaxprüfung,
CLI-Namespace- und Todo-Konsistenzprüfung bestehen. Sämtliche echten
SDK-Probeläufe hier verwendeten einen synthetischen containerlokalen Server.

Damit ist der optionale Provider-Unterbau PI-T02 abgeschlossen, nicht die
gesamte Pi-Integration. Hub-Policy-/Containerbindung, zentrale Modell- und
ContextBundle-Konfiguration, Ergebnis-Ingress und optionale echte Modellläufe
(PI-T03 bis PI-T06) bleiben offen. SRP/DIP werden durch getrennte Konfigurations-,
Runtime-, SDK-, Parser- und Prozessadapter mit injizierten Prüfports erhalten.

## PI-T03: Abgleich der noch offenen Policy-Anbindung

Der Quellabgleich gegen `359621c4a` findet bereits die benötigten zentralen
Bausteine: `HubProviderContextSpec` erzeugt Hub-seitig Provider-Kontexte;
`ProviderInvocationContext`, die Provider-Middleware und
`HubProviderBudgetAdapter` transportieren beziehungsweise prüfen Modell-,
Endpoint-, Budget- und Fencing-Bindungen. Native Workflow-Aufträge besitzen
bereits Hub-Revalidierung und öffentliche Ed25519-Verifikation. Diese Ports
sollen erweitert/verwendet werden, nicht durch eine Pi-eigene Task Queue,
einen symmetrischen Worker-Signierer oder eine zweite Modellkonfiguration
ersetzt werden. Die vorhandene allgemeine `legacy_compatible()`-Fallback-
Semantik ist für Pi ausdrücklich keine Autorisierungsgrundlage.

Vor Aktivierung fehlen weiterhin die konkrete Task-/Assignment-/Lease-
Komposition und eine nachgewiesene Containergrenze gegen fremde Prozesse.
Die bisherige boolesche Autorisierungsnaht ist nur ein Port, nicht selbst
der Nachweis einer gültigen Hub-Freigabe. Die vorhandenen anderen Legacy-
Aufrufpfade werden durch diese Arbeit nicht global umgestellt.

Zusätzlich muss Pi denselben Redirect-Schutz wie die vorhandenen Python-
Provider-Transporte erhalten. Das gepinnte SDK bietet dafür einen `fetch`-Port
in `ProviderRequestOptions`; der OpenAI-Completions-Adapter reicht ihn weiter.
Erforderlich sind ein exakt gebundener POST-Endpunkt, höchstens ein HTTP-
Aufruf, abgelehnte Redirects und ein echter automatischer Negativtest, der
einen unerlaubten Zielkontakt erkennt. Eine vorherige URL-Prüfung allein
schützt nicht vor späterem Folgen eines Redirects.

### Durchgesetzte HTTP-Grenze (2026-09-10)

Ein echter isolierter SDK-Negativlauf hat die Lücke bestätigt: HTTP 307
führte zu zwei Modellanfragen, einem Kontakt am Redirect-Ziel und einem
fälschlich erfolgreichen Provider-Ergebnis. Der separate Adapter
`pi_http_transport.mjs` wird jetzt über den öffentlichen SDK-`fetch`-Port
injiziert. Er erlaubt genau einen POST an den ausgewählten vollständigen
Completions-Endpunkt, prüft Modell, Streaming-Form und Tokenobergrenze im
tatsächlichen Request und lehnt Tools sowie zusätzliche Versuche ab.
`redirect: error` verhindert den Zielkontakt; auch ein injizierter Transport
darf keinen 3xx-/Redirect-/abweichenden Ziel-Response unterschieben.

Der identische echte 307-Negativlauf besteht nach der Änderung: eine Anfrage,
null Redirect-Zielkontakte, `pi_assistant_failed`, unverändertes Projekt und
vollständiges temporäres Cleanup. Reale normale und unerlaubte Tool-Antworten
bestehen ebenfalls mit genau einer Anfrage; der Tool-Versuch bleibt abgelehnt.
Alle drei nutzen Pi 0.85.1 mit synthetischem containerlokalem Modell, nicht
externe Inferenz oder produktive Release-Evidenz. Die 33 Node-Vertragsfälle
einschließlich eines echten lokalen HTTP-Redirects sind zusätzlich über
`tests/pi/test_pi_http_transport.py` im regulären Pytest-Gate erfasst.

SRP/DIP: Der kleine Transportadapter vollstreckt die bereits ausgewählte
Endpoint-/Budgetprojektion. Er entscheidet weder über Modellwahl noch über
Hub-Autorität. DNS-/Egress-Policy, konkrete Hub-Task-/Lease-Bindung und die
Containerkomposition bleiben separate offene Teile von PI-T03; der neue
Guard ist keine allgemeine OS-Netzwerksandbox.

### Konkrete nächste Kompositionsgrenzen

Der weitere Quellabgleich findet `CodingAgentInferenceTarget` zusammen mit
dem Aider-spezifischen Resolver und dessen globalen Hub-Konfigurationsimports
in einer Datei. Vor Verwendung im Worker-Policy-Adapter wird der reine DTO
in den bestehenden gemeinsamen Vertragsbereich ausgegliedert; der bisherige
öffentliche CLI-Namespace reexportiert ihn unverändert. Das beseitigt diese
SRP-/DIP-Kopplung, ohne Aider-Modellwahl oder globale Defaults umzuschalten.

`ProviderInvocationContext.from_value(None)` ist weiterhin ein absichtlicher
Legacy-Fallback für andere Aufrufer, keine Pi-Freigabe. Pi muss einen konkreten
Hub-Kontext mit exaktem Modell/Endpoint, Budget und Task-/Fencing-Bezug
verlangen. Strukturprüfung allein ersetzt weder Hub-Revalidierung noch
Dispatch-Autorität. Die vorhandene Native-Workflow-Komposition besitzt dafür
bereits Verify-only- und Hub-Budget-Ports; deren konkrete Bindung wird getrennt
von DTOs und Prozessausführung implementiert. Keine produktive Pi-Aktivierung
oder neue Autorität wird durch diese Vorbereitung eingeführt.

Der Zielvertrag liegt jetzt in `ananta_contracts/coding_agent_target.py`.
Der bestehende CLI-Import ist ein Reexport derselben Klasse; Felder,
Unveränderlichkeit, öffentliche Metadaten und explizite Prozessprojektion
bleiben erhalten. Ein separater isolierter Python-Prozess bestätigt, dass
der gemeinsame Import kein `agent`-Modul oder Hub-Konfiguration lädt.
46 Zielvertrags-/Aider-/Pi-Provider-/Runtime-Prüfungen bestehen in 36.66 Sekunden;
Ruff und Namespace-Detektor sind grün. Diese SRP-/DIP-Korrektur stellt noch
keine Task-Autorisierung oder Budgetreservierung bereit.

### Pflichtprojektion und Hub-Budget vor Pi-Ausführung

`PiInvocationPolicy` verlangt jetzt einen konkreten `ProviderInvocationContext`
mit Hub-Budgetpflicht, Workflow/Step/Attempt/Fencing, ausgewähltem Modell,
kanonischem Endpoint und Provider-Call-ID. Ein fehlender Kontext wird niemals
über `legacy_compatible` ergänzt. Die Policy kopiert den Kontext und nutzt den
bestehenden `ProviderBudgetPort`; sie besitzt weder Budgetledger noch Signierer.
Modell-/Endpoint-Abweichungen, Metadata-Ziele, nicht erlaubter externer Egress
und unsichere DNS-Auflösung werden vor Reservierung/Prozessstart abgelehnt.
DNS-Vorprüfung ist ausdrücklich kein Address-Pinning oder OS-Netzwerksandbox.

Eine explizite endliche Deadline ist Pflicht; eine kürzere `expires_at` im
Autorisierungsumschlag begrenzt sie zusätzlich. Der Provider revalidiert die
injizierte Task-Autorität vor Vorbereitung, vor und nach Budgetreservierung
sowie nach Ausführung. Abgelaufene oder widerrufene Ergebnisse werden nicht
veröffentlicht. Die Prozessfrist umfasst auch die verbrauchte Vorbereitungs-
und Revalidierungszeit. Unterstützt ist weiterhin genau ein No-Tools-Turn;
zusätzliche kombinierte Retry-Protokolle werden nicht stillschweigend übergangen,
sondern bis zu ihrer konkreten Komposition geschlossen abgelehnt.

Die Ausgabegrenze ist `min(1024, Hub-Maximum)` und wird in die tatsächliche
SDK-Modellkonfiguration geschrieben. Die Reservierung berücksichtigt den
UTF-8-Umfang von Prompt und gepinnter SDK-Systemrahmung plus 256 Tokens
Framingreserve; das ist eine konservative Schätzung, keine tokenizer-exakte
Verbrauchsmessung. Eingaben oberhalb des Hub-Budgets oder des konservativen
8192er Kontextprofils werden abgelehnt. Unbekannter Verbrauch wird nicht
erstattet. Eine Policy-Instanz kann nur einmal reservieren, auch nach unklarer
Hub-Antwort; verteilte Replay-Sicherheit bleibt Aufgabe des Hub-Ports.

Mit echtem Pi 0.85.1 im begrenzten, nichtprivilegierten Offline-Testcontainer
erreicht eine synthetische Freigabe über 37 Ausgabetokens den Modellrequest
unverändert: eine Reservierung, eine Anfrage, unverändertes Projekt, Cleanup.
Eine synthetische Budgetablehnung erzeugt null Modellanfragen. Der echte
307-Negativlauf bleibt bei einer Anfrage und null Redirect-Zielkontakten.
Diese Beobachtungen sind keine produktive Hub-/Release-Evidenz.
79 Policy-/Provider-/Runtime-/HTTP-Prüfungen bestehen in 56.63 Sekunden;
Ruff, Namespace-Detektor und Todo-Konsistenzprüfung sind grün.

SRP/DIP: Policyprojektion/Reservierung liegen getrennt von Prozesslebenszyklus
und privater Konfigurationsmaterialisierung. Die bestehende breite
Provider-Komposition wird nicht zu einem zweiten Orchestrator erweitert.
Konkrete Hub-Task-/Lease-Verifikation, Containerkomposition, Kontextbundle-
Zuführung und Result-Ingress bleiben offen; ein gültiger DTO oder synthetischer
Budgetport ersetzt diese Bindungen nicht. Pi bleibt standardmäßig deaktiviert.

### Hub-Transport vor konkreter Pi-Komposition

Ein echter synthetischer Zwei-Server-Negativtest hat im vorhandenen
`HttpWorkflowHubDecisionClient` eine weitere Lücke reproduziert: HTTP 302
wurde als GET zum fremden Port verfolgt, inklusive Authorization-Header;
die dortige `allowed: true`-Antwort erreichte den Client. Das betrifft den
gemeinsamen Workflow-Hub-Transport, nicht den bereits gesperrten Pi-Modell-
Redirect. `workflow_hub_http.py` kapselt jetzt einen Redirect-sperrenden
Opener mit unverändertem TLS-Kontext und prüft zusätzlich das Antwortziel.
3xx endet begrenzt mit `workflow_hub_redirect_denied`, ohne automatischen
Retry und ohne Lesen einer vermeintlichen Ziel-Freigabe.

Alle fünf echten 301/302/303/307/308-Negativfälle bleiben bei null Kontakten
zum zweiten Server; der direkte authentifizierte POST funktioniert weiter.
29 Transport- und registrierte-Worker-Service-Auth-Prüfungen bestehen in
25.68 Sekunden. SRP: Der kleine HTTP-Guard ist von Gateway-Domänenadaptern
getrennt; deren vorhandene breite gemeinsame Kompositionsdatei bleibt
bestehende SRP-Schuld. Keine Änderung an Hub-Entscheidungen oder Lease-Eigentum.

Der konkrete Budgetadapter prüft außerdem den Bezug der Hub-Antwort zur
Anfrage: Reservierungs-ID, exakte Token-/Kostenreservierung, nicht bereits
abgerechneter Zustand und gültige aggregierte Grenzen. Die Abrechnung darf
ebenfalls nur dieselbe Reservierung bestätigen. Zehn synthetische negative
Varianten wurden zuvor akzeptiert und werden jetzt geschlossen abgelehnt;
53 Budgetbeleg-, Worker-Inferenz- und echte Hub-Gateway-Service-Prüfungen
bestehen in 40.40 Sekunden. Die Persistenz- und Profilzähler verbleiben
unverändert beim bestehenden Hub-Budgetdienst.

### Signiertes Hub-Profilbudget konkret angeschlossen

Der Quellabgleich des bestehenden `provider_budget_reserve`-Kommandos zeigt:
Es reserviert Profilversuch, Node- und Run-Budget bereits atomar unter dem
signierten `provider_attempt_plan`. Dafür ist kein weiterer Worker-Zähler
und kein separates Retry-Kommando nötig. Pi akzeptiert nun auch den von
`HubProviderContextSpec` ausgegebenen Pflicht-Profilkontext; kombinierte
Retries bleiben abgelehnt, das SDK führt weiterhin genau eine Anfrage aus.

Die neue Kompositionsprüfung verbindet Pi mit `HubProviderBudgetAdapter` und
dem tatsächlichen `WorkflowWorkerGatewayService`, inklusive Hub-seitigem
Testsignierer, Grant-, Ownership-, Assignment-, Event- und Budgetdienst.
Der Worker-Testadapter erhält keine Signierschlüssel. Ein Profilversuch
funktioniert; eine zweite Pi-Instanz erhält keinen zweiten Versuch. Fremder
Worker, falscher Attempt/Fence, manipulierte Signatur und nicht gewähltes
Profil werden vom Hub vor Modellprozessstart abgelehnt. 58 Profilbudget-,
Pi-Policy- und Budgetbelegprüfungen bestehen in 43.30 Sekunden. Das sind
automatische synthetische Kompositionsprüfungen, noch keine vollständige
registrierte Pi-Task-/Container- oder produktive Release-Abnahme.

### Ausführungsadapter für einen delegierten Native-Task

`NativePiNodeHandler` implementiert den vorhandenen `NativeNodeHandlerPort`.
Er verlangt `pi_coding_agent`, die explizite Capability `coding.agent.pi`,
den identischen Task-Snapshot und den bereits Hub-validierten Profilkontext.
Workspace- und Credential-Zugriff erfolgen über kleine injizierte Ports;
Modell, Endpoint, Profil, Attempt und Fence werden nicht lokal ausgewählt.
Fristen können nur gegenüber Node-Budget und Hub-Autorisierung verkürzt werden.
Das Ergebnis nutzt den bestehenden Native-Result-/Verification-Vertrag und
trägt keine erfundenen Artefakte oder Verbrauchsmessungen.

Die Kompositionsprüfung führt diesen Handler durch `NativeGraphWorkerTaskAdapter`,
`NativeDelegatedNodeRuntime`, Verify-only-Nonce-Prüfung, `NativeHubExecutionScope`
und den echten Hub-Gateway-/Budgetdienst aus. Replay und fremde Worker laufen
nicht bis Pi; unaufgelöste Kontext-/Artefaktreferenzen, fehlendes Prompt und
Schreibanforderungen werden ausdrücklich abgelehnt. Eine zusätzliche negative
Prüfung reproduzierte die bisherige Umwandlung von `allowed: "false"` in eine
Freigabe. Der gemeinsame Native-Scope akzeptiert jetzt ausschließlich Boolean
`true`. 34 Native-/Profilbudget-/bestehende Adapterprüfungen bestehen in
29.23 Sekunden; nach Aufteilung der Handler-Methoden bestehen dessen zehn
Fälle erneut innerhalb eines 28er Gates inklusive Worker-Konfiguration.

SRP/ISP/DIP: Taskprüfung, Ziel-/Fristprojektion und Ergebnisabbildung bleiben
kleine Adaptermethoden; Hub-Scope, Workspace, Credentials und Provider sind
injiziert. Es entstehen weder Task Queue noch Worker-Orchestrierung.
Der Handler allein aktiviert noch keinen laufenden Worker: explizite
Deployment-Konfiguration, Fabrikverdrahtung und Containerabnahme folgen.

### Explizite Worker-Fabrik und Deployment-Profil

Die bestehende Native-Fabrik verwendet jetzt eine kleine Task-Kind-Handler-
Zuordnung. Nur `pi_coding_agent` geht an Pi; deaktivierte Pi-Aufträge erhalten
`pi_disabled` und werden niemals als Shell-Auftrag oder anderer Provider
interpretiert. Bestehende Task-Kinds behalten ihren bisherigen Handler.
Die Hub-Task-Queue, die Worker-Auswahl und der Native-Result-Ingress ändern
sich nicht. Die Zuordnung ist lokale Operationsauswahl, keine Orchestrierung.

`worker_runtime.native_graph.pi` enthält ausschließlich Deployment-Opt-in,
Scratch-Verzeichnis und Profil-ID-zu-Schlüsseldatei-Zuordnung, keine Modellwahl.
`enabled` muss Boolean sein; ein aktiviertes Profil benötigt Task-Kind und
Capability explizit. Schlüssel werden nur aus der konfigurierten, begrenzten,
gegen Symlinks/Schreibrechte abgesicherten Datei geladen. Fehlende Dateien oder
Profile führen nicht zu einem Rückgriff auf `OPENAI_API_KEY` oder andere
Umgebungswerte. Deaktiviertes Pi wird nicht als Capability registriert.
Alte Worker-Profile behalten ihre bisherige Projektion ohne zusätzliches
`pi: null`-Feld. Der gepinnte Laufzeit-Probe bleibt vor jedem Modellprozess
Pflicht; eine deklarierte Capability ersetzt keinen erfolgreich geprüften
Pi-Paketstand.

Das separate Beispiel `config/workflow_runtime/pi_worker_profile.v1.json`
wird nur durch ausdrückliche Auswahl über
`ANANTA_WORKFLOW_ADAPTER_WORKER_PROFILE_FILE` aktiv. Es gehört in einen
dedizierten Worker-Container mit eigenem `ANANTA_WORKSPACE_ROOT`, bestehender
registrierter Hub-Service-Identität und Verify-only-Autorisierung sowie
bereitgestelltem Pi 0.85.1. Die Profil-ID `local_ollama_phi4_mini` muss der
Hub-Auswahl entsprechen; `/run/secrets/pi_local_provider_key` wird als
geschützte Datei nur diesem Worker bereitgestellt. Für einen lokalen Provider
ohne Authentifizierung ist ein expliziter unprivilegierter Platzhalterwert
möglich, nie ein heimlich erzeugter Default. Abschalten: `pi.enabled: false`
oder das bisherige Native-Profil wählen und den Worker neu starten.

Die neue Quelldatei war im geteilten Checkout gruppenbeschreibbar; der strikte
Profil-Ladetest lehnte sie korrekt ab. Der Quickstart-Build setzt deshalb die
drei eingebauten Profile explizit per `COPY --chmod=0444`, unabhängig von der
Checkout-Umask. 43 initiale Worker-/Native-Konfigurationsfälle bestehen;
abschließend 17 Fabrik-/Registrierungsprüfungen (18.07 s), 19 Konfigurations-
und Dateigrenzprüfungen (18.97 s) sowie 9 Image-Vertragsprüfungen (12.71 s).
Die Gates überlappen und sind keine addierbare Fehlerzahl. Ruff, Namespace-
und Todo-Prüfung sind grün. Ein realer, getrennt containerisierter Pi-Task
und ContextBundle-Zuführung bleiben die nächsten Abnahmen.

### Getrennte Containerabnahme des begrenzten Sicherheitsprofils

Die nächste Abnahme verwendet den unveränderten Quellstand
`325636c0a4bc3d9f88fcd83c927ba9fb07ae5951`, Pi 0.85.1 und Node 24.18.0.
Hub, Worker und synthetischer Modellserver laufen in getrennten, nicht
privilegierten Containern: UID 65534, schreibgeschütztes Root-Dateisystem,
keine Capabilities, `no-new-privileges`, 768 MiB, 0.5 CPU und 64 Prozesse
je Container. Das interne Docker-Netz hat keine veröffentlichten Ports.
Nur der Worker erhält das gepinnte Pi-Paket schreibgeschützt; weder GPU,
Docker-Socket noch das Benutzerprojekt werden eingebunden. Die drei
eingebauten Worker-Profile haben im Image tatsächlich Modus 0444.

Die private Test-Hub-Hülle verwendet den echten Gateway-, Grant-,
Assignment-, Ownership- und Budgetdienst. Ihr Signierschlüssel entsteht
zufällig ausschließlich im Hub-Speicher. Der Worker hat keinen Zugriff
darauf und verwendet Native-Fabrik, Task-Consumer, Hub-HTTP-Revalidierung,
Budgetadapter und den tatsächlichen Pi-SDK-Prozess. Lediglich Taskzustellung,
Uhr und Modellantwort sind deterministische Testadapter; dies ist noch keine
Abnahme der vollständigen produktiven Flask-Registrierung/Task-Ingress-Kette.

- Normalfall: bestanden in 4.010 Sekunden; genau eine Modellanfrage mit
  Ausgabelimit 32, gebundenes Native-Ergebnis, Replay abgewiesen, fremde
  Projektkonfiguration unverändert und temporärer Laufzustand aufgeräumt.
- Zweiter separater Worker am selben Hub: erwartetes `pi_hub_budget_denied`
  in 1.970 Sekunden; insgesamt weiterhin nur eine Modellanfrage. Das Limit
  wirkt damit auch prozessübergreifend, nicht nur durch eine lokale Sperre.
- Frischer Lauf mit bösartigem Modell-Toolvorschlag: erwartetes
  `pi_tool_execution_denied` in 3.990 Sekunden; genau eine Modellanfrage,
  keine angeforderte Datei, keine Artefakte/Teilantwort und vollständiges
  Aufräumen des Laufverzeichnisses.

Testimage: `sha256:6e70cac9216c9a765531e358cedfab21c044bf36f91c67620e817357acbfa8cb`.
Reproduktionsmaterial bleibt lokal unter `data/pi-native-reference.uHZBM2bQ`;
die fünf eigenen Testcontainer und ihr internes Netz wurden entfernt.
Es waren ausschließlich flüchtige Testzustände, keine Benutzerdaten.

PI-T03 ist damit für das ausdrücklich begrenzte No-Tools-/Read-only-Profil
abgeschlossen. Direkte Datei-Tools bleiben unsupported; eine allgemeine
OS-Sandbox oder freigegebene Produktionsinferenz wird nicht behauptet.
Diese unreservierten synthetischen Beobachtungen ersetzen keine Hub-
registrierte Release-Evidenz. ContextBundle-Transport, API-/Modellprojektion
und verifizierter Evidenz-/Result-Ingress bleiben PI-T04 bis PI-T06.
SRP/DIP bleiben durch die vorhandenen Hub-Ports und kleinen Worker-Adapter
erhalten; die breite bestehende Native-Kompositionswurzel wird nicht durch
eine neue Orchestrierung ersetzt.

### Kontextfilter: ausstehende Freigabe ist kein Grant

Vor PI-T04 wurde der vorhandene Hub-Kontextfilter gegen automatische negative
Fälle geprüft. Er transportierte bisher jede Entscheidung außer `deny`, also
auch eine tatsächlich vom Evaluator wegen falschen Inhaltsbezugs als
`approval_required` bewertete Freigabe. Fünf negative Varianten schlagen vor
der Korrektur reproduzierbar fehl (12.41 s); das ist eine gemeinsame
Filterlücke, nicht fünf unabhängige Fehler.

Der Filter akzeptiert jetzt ausschließlich `allow`, `allow_redacted` und
`allow_summary_only`. Ausstehende Freigaben, nicht verfügbare Entscheidungen
und unbekannte Zustände liefern sofort keinen Kontext. Bestehende Redaktions-
und Zusammenfassungspflichten bleiben erhalten; die Eingabeblöcke werden
nicht verändert. Die Tests verwenden ausschließlich automatische Policy-
Fixtures und Test-Doubles, keine menschlichen Bestätigungen.

28 neue und bestehende Kontextfilter-/Policy-/Worker-Grenzprüfungen bestehen
in 31.17 Sekunden. Ruff ist für die neue Testdatei grün; die bestehende
breite Service-Datei hat weiterhin 14 bereits vorhandene Import-/Format-
Meldungen. Ihr SRP-/DIP-Altschuldenstand (Persistenz, Klassifikation und
Policy-Fassade in einem Service) bleibt ausdrücklich bestehen. Die Korrektur
ändert nur die geschlossene Grant-Auswahl; sie führt keine neue Policy-
Autorität ein. Der Pi-ContextBundle-Transport ist damit noch nicht fertig.

### Aufgabenbezogener Hub-Transport und Worker-Revalidierung

Das additive Kommando `native_context_read` verwendet den bestehenden
authentifizierten Workflow-Worker-Gateway. Es verlangt auch dort, wo ältere
Kommandos direkte Testkomposition erlauben, eine registrierte Worker-Identität.
Aktive Ownership, Assignment einschließlich exakter Hub-Task-ID, Attempt,
Fence und signierte Hub-Freigabe werden vor dem Kontextzugriff geprüft.
`NativeContextBundleService` liest ausschließlich den gespeicherten Native-
Auftrag mit passendem Tenant/Projekt und verwendet den vorhandenen
`TaskContextBundleAccessService` für die persistierte Bundle-Aufgabenbindung.
Die Ziel-Policy ist ein eigener injizierter Port, keine Worker-Entscheidung.

Die geschlossene Projektion enthält höchstens 24.000 UTF-8-Bytes sowie Task-,
Bundle-, vollständigen Command-, Policy- und Inhaltsbezug. Diese Hashes sind
Integritätsbindungen, keine SRC-/RUN-Identitäten und keine Freigabe für sich.
Das Audit protokolliert nur die Bezüge, nie den Kontexttext. Unbekannte,
fehlende oder mutierte Felder werden nicht stillschweigend übernommen.

Der Pi-Worker fügt den freigegebenen Text als strukturierten Prompt-Inhalt
ein; Systemprompt, Tools und Extensions bleiben unverändert geschlossen.
Vor Modellstart, vor/nach Budgetreservierung und nach dem Prozess wird die
Hub-Projektion erneut gelesen und exakt verglichen. Geänderte Policy,
geänderter Inhalt oder Widerruf stoppen den Lauf beziehungsweise verwerfen
die Modellantwort. Fremde Projektdateien und ungebundener Kontext werden
auch dabei nicht geladen.

30 reale Hub-Gateway-/Bundle-Vertragsfälle bestehen in 25.35 Sekunden.
21 neue Pi-Kontextfälle plus 20 bestehende Native-/Fabrikfälle bestehen in
32.94 Sekunden; das überlappende Hub-/Budget-/Service-Auth-/Native-Gate mit
86 Fällen besteht in 60.93 Sekunden. Alle Prüfungen sind automatisch und
synthetisch. Ruff ist für die neuen/geänderten Transportdateien grün.

SRP/ISP/DIP: Persistierte Aufgabenbindung, Ziel-Policy, Transportvertrag und
Worker-Verbrauch sind getrennte kleine Adapter. Der bestehende breite
Workflow-Gateway bleibt eine Kompositions-/Dispatch-Fassade; seine bestehende
SRP-Schuld wird nicht durch neue Datenbank- oder Retrieval-Logik vergrößert.
Noch offen ist die produktive Komposition mit aktiven Projekt-Policies und
dem Hub-Zielkatalog. Ohne diesen Policy-Port liefert der Hub ausdrücklich
`native_context_service_unavailable`, niemals eine Ersatzfreigabe.

### Aktive Projekt-Policy und bestehender Hub-Zielkatalog

Die produktive Gateway-Fabrik verbindet den Reader jetzt mit der bestehenden
Repository-Registry, dem SQL-Context-Policy-Lifecycle und dem app-eigenen
`source_control_destination_catalog`. Der aktuelle Katalog wird bei jedem
Zugriff neu aufgelöst; ein anderer Flask-App-Kontext erbt keine Freigaben.
Fehlt der Katalog, bleibt der Zugriff begrenzt gesperrt. Aufgaben ohne aktiven
Ausführungsstatus werden ebenfalls vor dem Policy-Zugriff zurückgewiesen.

Das Hub-gespeicherte Bundle benennt unter
`bundle_metadata.native_context_access` ausschließlich `policy_id`,
`destination_id` und den exakten `provider_endpoint_identity`. Dies sind
Selektoren, keine Grants. Der Reader verlangt eine aktive, digest-konsistente
Policy im selben Tenant/Projekt und den passenden registrierten Worker,
Inferenzanbieter und Modellnamen. Die Antwort bindet Policy-Version,
Policy-Digest, Zielkatalog-Digest und Endpoint gemeinsam. Es werden keine
Policies aktiviert, Modelle ausgewählt oder Berechtigungen ausgestellt.

Ein eigener kleiner Chunk-Adapter versteht sowohl die bestehende persistierte
CodeCompass-FTS-/Graph-/Repository-Map-Form als auch bereits klassifizierte
CAP-Blöcke. Er verwendet den bestehenden Hub-Klassifikations-/Policy-Dienst.
Fremde Promptzusammenstellungen aus `context_text` werden nicht übernommen.
Widersprüchliche Felder und Approval-Marker werden abgewiesen; erkannte
Secrets können sich nicht hinter einer öffentlichen Klassifikation verstecken.
Redaktion beziehungsweise Zusammenfassung erfolgt vor der Ausgabe. Die
Source-Control-Ortsklassen werden ausdrücklich abgebildet: unbekannte oder
externe Ziele dürfen nicht durch die ältere String-Heuristik lokal erscheinen.

Das Pi-Profil verlangt ausdrücklich eine Sendefreigabe; reine Lese-/Schreib-
Rechte genügen nicht. Eine passende `approval_required`-Regel liefert sofort
einen maschinenlesbaren Fehler statt eines interaktiven Dialogs. Vollautomatisch
erlaubte Läufe verwenden eine ausdrücklich aktive Hub-Policy ohne diese
ausstehende Freigabe. Das erweitert keine bestehenden Berechtigungen.

73 gezielte Hub-/Policy-/Persistenzfälle bestehen in 53.46 Sekunden. Nach dem
Abgleich mit der tatsächlichen CodeCompass-Chunkform bestehen 86 Policy-,
Native-Pi-, bestehende Taskadapter- und Retrieval-Vertragsprüfungen in
60.13 Sekunden. Das schließt eine echte SQL-Policy-Aktivierung/Widerruf und
deren Verwendung durch Native-Worker, Hub-Budget und Pi-Prozess-Testdouble
ein; keine menschlichen Schritte und keine Produktions-Evidenzbehauptung.
Ruff ist für diese Änderungen grün. Die Hub-seitige automatische Vorbereitung
neuer Pi-Aufgaben-Bundles sowie Modell-/Statusprojektionen bleiben noch offen.

## Automatic task-context preparation audit

At `71c31f52d`, the authenticated context read and active-policy composition
are present, but the Native Hub queue does not prepare a child-task-owned
bundle or copy the control task's project scope. Legacy `control_task_id`
values are sometimes runtime identities rather than actual Task rows; do not
turn those into unconditional relational parent references.

Add an explicit `context_bundle_mode=control_task` path for Pi nodes. It must
resolve a real same-tenant, project-scoped control Task and its existing owned
bundle, preserve the original owner, and create an idempotent bounded child
snapshot through a narrow Hub persistence port. Copy the complete existing
organization scope, with `team_id` passed through the queue's dedicated
argument. Active policy and destination selectors are references, not grants;
the authenticated read still decides whether any text can reach the model.
Do not permit Worker retrieval, fallback context, synthetic evidence issuance,
or a missing preparation port to silently omit requested context.

Also tighten duplicate submission checks from command ID alone to the full
persisted command. Preserve the no-context legacy path and its lack of assumed
parent/plan foreign keys. The existing queue adapter combines submission,
polling and cancellation (preserved SRP debt); context normalization and
persistence belong behind separate ports, not additional queue responsibilities.
This audit is a plan for the next bounded implementation, not its completion.

The follow-up audit found an actual persistence-contract defect in the new
context reader: `TaskDB` has no `source` column. Earlier dictionary fixtures
supplied one and hid that mismatch. A new test using the real model failed
with `native_context_task_binding_mismatch` (7.72 seconds). The reader now
requires the existing persisted `task_kind=pi_coding_agent`,
`derivation_reason=native_graph_hub_delegation`, worker-context schema and
`runtime_path=native_graph_node`, in addition to the unchanged exact command,
registered assignment, current lease, task scope and active-policy checks.
No database column or migration is invented. Wrong persisted markers remain
denied before policy evaluation. All 65 gateway/Native/persistent-policy
regressions pass in 47.79 seconds; selected Ruff passes. This corrects the
earlier source-field claim; automatic bundle preparation remains separate.

## Automatic task-context preparation implemented

Pi nodes may now explicitly select `context_bundle_mode=control_task`,
`context_policy_id` and `context_destination_id` in their Hub-owned node
metadata. The existing Native queue delegates preparation to a narrow Hub
service. It resolves an active, persisted same-tenant/project control Task,
uses the existing task/bundle ownership boundary, and preserves the complete
organization scope. Invalid partial scope is rejected; the queue receives
`team_id` through its existing dedicated argument. Nodes without these
selectors keep their legacy path without assumed Task/Plan foreign keys.

The Hub writes a separate child-owned snapshot of at most 32 canonical chunks
and 24 KiB serialized context. It never rebinds the original bundle or copies
its assembled context, arbitrary metadata, approval overrides or secrets into
Worker configuration. Normalization is not an LLM grant: active destination
policy still classifies and transforms every chunk before authenticated reads.
Snapshot metadata binds the full command and source owner/chunk digest; these
ordinary hashes and `nctx-*` IDs are not `SRC_*`/`RUN_*` evidence identities.
Reads revalidate snapshot content, policy selectors, parent and command before
returning any projection. The Worker still has no repository or retrieval port.

Snapshot persistence accepts only an identical existing row, including a
concurrent insert. If task ingestion fails after the snapshot commit, a retry
reuses the exact snapshot; a changed source fails closed instead of replacing
it. This bounded retry behavior does not claim a new cross-repository database
transaction. Duplicate queue submissions now compare the complete canonical
command, including JSON value types, rather than its ID alone.

Validation: the first 102 preparation/queue/Native/gateway regressions passed
in 43.99 seconds. The expanded 158-case run passed 155 and exposed three
incorrect test assertions about early Native failures (which correctly return
an empty result object); these were corrected, not runtime guards weakened.
The final 42 preparation/composition checks pass in 24.46 seconds, including
actual SQL bundle persistence, real Task models, the real queue ingestion
adapter and the complete prepared-task -> active SQL policy -> Native Pi
path. Changed content, foreign parent and revoked policy produce the exact
bounded denial before credentials, budget reservation or model execution.
Selected Ruff and the CLI namespace detector pass. These are synthetic,
unreserved technical tests, not production release evidence.

SRP/DIP: snapshot normalization, persistence adaptation and queue orchestration
remain separate. Existing broad queue/lifecycle services are preserved debt;
no new Worker orchestration, global cache or security decision owner is added.
PI-T04 remains open for explicit provider compatibility and API/UI projections;
PI-T05/06 retain their evidence/result and final acceptance work.

## Provider and status projection audit

Official endpoint checks on 2026-09-10 confirm the chosen chat-completions
transport: Ollama uses `/v1/chat/completions` (normally port 11434) and documents
`max_tokens`; its local example requires an SDK key value which the server
ignores. A deployment token must still come from the explicitly configured
task profile, not ambient credentials.
[Ollama compatibility](https://docs.ollama.com/api/openai-compatibility).

LM Studio likewise documents `/v1/chat/completions`, normally port 1234, with
`max_tokens` and streaming. Authentication is optional by default, with API
tokens available from version 0.4.0; Ananta must not interpret a configured
placeholder as proof that a protected server accepted it.
[LM Studio transport](https://lmstudio.ai/docs/developer/openai-compat),
[parameters](https://lmstudio.ai/docs/developer/openai-compat/chat-completions),
[authentication](https://lmstudio.ai/docs/developer/core/authentication).

OpenRouter documents HTTPS `/api/v1/chat/completions`, Bearer authentication
and a concrete organization-prefixed model ID. Its default server-side
provider fallback is independent of Pi's disabled client retries. The strict
one-call profile therefore needs explicit `allow_fallbacks=false` and
`require_parameters=true`, without model arrays, automatic model selection or
unbound routing overrides. This still selects the OpenRouter service, not a
claim that Ananta controls its physical GPU or grants regional processing.
[API](https://openrouter.ai/docs/api_reference/overview),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

The pinned Pi SDK supports `compat.maxTokensField` and passes
`compat.openRouterRouting` into the request's provider field. The current
Ananta configuration does not set those compatibility fields explicitly;
hostname-based SDK defaults must not decide whether a Hub token ceiling is
sent using the officially documented parameter. Add deterministic provider
configuration and transport regressions, then inspect the actual pinned SDK's
requests with synthetic responses; no paid provider contact is authorized by
this test and no live provider acceptance is claimed.
[Pinned Pi model contract](https://github.com/earendil-works/pi/blob/05c6229813414010445558db9a80c84e15d65e70/packages/coding-agent/docs/models.md).

Status/UI remains a separate additive step. Reuse the registered-Worker
`/backends/pi/provision` status path and existing setup surface. Installation,
deployment opt-in, configured task-profile credentials and actual authorized
inference are distinct states. Never probe the Hub's local binary as a
Worker's readiness, expose credential paths/content or enable global automatic
backend routing. Pi remains `open_source_byok`; inference cost is separate.

The provider correction now explicitly selects `max_tokens`, a system role,
no storage, no reasoning-effort field and no optional streamed-usage request.
Finish reasons remain mandatory. OpenRouter receives exactly the two strict
routing restrictions above. The HTTP boundary rejects missing/broadened
routing, local routing overrides, model arrays and the alternative completion-
token field. It retains one attempt, exact endpoint/model, cancellation,
no-tools and redirect rejection. The routing decision is reduced to an
immutable flag, not a mutable caller policy reference.

Before correction, three configuration checks and the Node-wrapper check
failed in 9.80 seconds; the final pre-fix Node selection failed nine of 42
assertions in 0.198 seconds. Afterwards all 70 Python provider/policy/Native
context checks passed in 34.08 seconds, and all 48 Node transport cases passed
in 0.205 seconds. These overlapping gates must not be counted as 118 distinct
independent runtime defects or complete provider acceptance.

A separate restricted container then executed actual pinned Pi 0.85.1 for
each of the three generated configurations. It had no network or GPU, UID
65534, read-only root, no capabilities, no-new-privileges, 0.5 CPU, 768 MiB,
64 PIDs and a 64-MiB tmpfs. Its base image was `6e70cac9216c`; the three
changed adapter files and pinned package were mounted read-only, so this is
explicitly a source-overlay diagnostic, not a newly packaged release image.
A test preload supplied synthetic SSE through the HTTP seam and asserted the
actual SDK request: one POST, selected model, Bearer value, `max_tokens=37`,
no tools/store/extra model routing and the exact OpenRouter restrictions.
The unchanged protocol parser accepted each complete response. Ollama-shaped,
LM-Studio-shaped and OpenRouter-shaped checks passed in 2.171 / 2.210 / 2.006
seconds respectively; ephemeral configuration was removed. The initial private
probe had omitted two required parser arguments and was corrected before this
successful run. No external server, paid account or actual model inference
was contacted, and no retroactive evidence identity is claimed.

## Worker readiness projection

The existing admin-only Hub-to-registered-Worker provisioning status response
now adds `native_execution` for Pi. A pure, content-free projection combines
the existing exact 0.85.1 installation probe with the already initialized
Worker runtime registration. It does not read credentials, rebuild adapters,
start inference, install packages or inspect a local Hub binary. Disabled Pi
is excluded by the existing runtime composition even when its capability
appears in configuration; an actual initialized-runtime regression verifies
both enabled and disabled cases.

`ready_for_assignment` means configured, not authorized or inference-verified.
Authentication remains `profile_configured_unverified` or
`task_profile_required`; cost remains provider-dependent and the client is
`open_source_byok`. Headless/structured output are supported, while tools,
MCP, workspace writes and resume remain unsupported. Every run still needs a
Hub assignment and policy; global automatic routing stays disabled. Unknown,
malformed, wrong-version or absent observations cannot become ready.

All 63 focused provisioning, API, capability and real runtime-composition
checks passed in 41.77 seconds; Ruff and the CLI namespace guard passed.
They are synthetic technical observations, not production release evidence.
SRP keeps presentation policy in a small pure module; the existing broad
SGPT route module remains preserved structural debt with only additive
composition wiring. The Angular consumer is a separate subsequent change.

### Anzeige und Grenzen in der Oberfläche

Unter den vorhandenen CLI-Backend-Einstellungen steht eine eigene Pi-Karte.
Sie zeigt je registriertem Worker die geprüfte Paketversion, den Native-Status
und die ausdrücklich noch ungeprüfte Anbieter-Authentifizierung. Modellwahl
und Zugang bleiben Sache des Hub-Auftragsprofils. Die Karte zeigt keine
Schlüssel, Credential-Pfade, Rohdiagnosen oder Modellantworten an und bietet
keine verdeckte Installation, Inferenz oder globale Aktivierung an.

Ein Klick auf „Pi-Status aktualisieren“ führt ausschließlich Statusabfragen
über den Hub aus: höchstens vier parallel, 32 unterschiedliche Worker und
15 Sekunden pro Anfrage. Offline-Worker werden nicht kontaktiert. Hub-Wechsel,
erneute Abfrage und Komponentenabbau brechen alte Abonnements ab; fremde,
verspätete, leere oder widersprüchliche Antworten bleiben unverifiziert.
Auch eine fehlgeschlagene Aktualisierung entfernt einen früheren Bereitschafts-
status. Kein Test benötigt einen Klick durch einen Menschen.

Die reine Anzeigeprojektion ist von Netzwerk-Lebenszyklus und Template
getrennt (SRP/DIP). Vorhandene gemeinsame Card-, Notice- und Badge-Bausteine
werden wiederverwendet; Pi bleibt fachlich lokal statt eine neue globale
„generische“ UI zu schaffen. Die breite bisherige Codex-/Claude-Komponente
bleibt bestehende strukturelle Schuld; hinzu kommen nur Import und Inputs.

36 gezielte UI-Prüfungen einschließlich echter Angular-Template-Darstellung,
Timeout, Hub-Wechsel, Abbruch, Fehlerredaktion und Begrenzung bestanden in
2,21 Sekunden. ESLint und die vollständige Angular-Template-/Typprüfung
bestanden mit Node 24.18.0 im netzlosen Read-only-Container. Eine bestehende
Warnung betrifft einen ungenutzten RouterLink in KnowledgeHygiene, nicht Pi.
Die ersten Container-Aufrufe benötigten eigene temporäre Vite-Verzeichnisse
und die vorhandenen benachbarten JSON-Schemata; das waren Prüfumgebungsfehler,
keine Produktdefekte. Keine laufende Installation oder GPU wurde verändert.

Damit ist PI-T04 abgeschlossen. PI-T05 (Hub-Evidenz-/Ergebnisbindung) und
PI-T06 (abschließende Verifikation und Einführung) bleiben offen; weder diese
Anzeige noch synthetische Providerprüfungen sind eine Produktionsfreigabe.

## PI-T05 source audit: results before evidence

At `89ea0429a`, Pi's provider already emits one existing `CodingAgentEvent`
only after complete protocol validation, post-execution authorization and
secret redaction. Partial SDK deltas are not authoritative; malformed,
contradictory or failed terminals are rejected. Resume remains explicitly
unsupported in the isolated single-turn/no-tools profile, rather than
accepting an unbound session ID or uploading it publicly.

The Hub queue poller currently checks only `hub_task_id` before returning a
stored Native result. The later orchestrator checks more correlation fields,
but this does not validate the Pi model/output contract, a conflicting Task
terminal state or coercive wire values such as a boolean fencing token. The
forwarded-result path also copies the nested Native verification without a
Pi-specific admission boundary. These are prerequisites, not successful
evidence ingestion. Add a small strict Pi result validator with regression
tests before wiring current persisted Task/assignment/lease acceptance.

Workflow run IDs, Native result IDs and provider-call IDs are ordinary runtime
correlations, not registry evidence. PI-T05 must separately integrate existing
Hub source admission and pre-execution run reservation with the actual
dispatched assignment and lease. Workers receive only the registry's closed
projection. Source content, policy, repository revision and execution
environment need immutable Hub-owned bindings; an arbitrary caller digest
or a retroactively registered successful command is insufficient. Reuse the
existing registry and assignment stores, not a new Worker registry/scheduler.
Exact idempotent results may be accepted; changed, expired or replaced
assignments must be rejected before publication. Test/synthetic scope cannot
become production release evidence.

Implementation proceeds through strict result contracts, Hub admission and
reservation, Worker projection/verified ingress, then focused automatic
composition tests. Existing broad forwarding/runtime services remain SRP/DIP
debt: add small ports and adapters instead of embedding the lifecycle there.

The first result boundary is now implemented as `pi_native_result_validation`.
Pi polling requires the exact complete wire schema, all eight command/Task
correlations, an integer fencing token without boolean/string/float coercion,
matching terminal Task status and the Hub-selected model. Its no-tools output
is closed and bounded: no artifact or Worker-budget claims, side effects,
extra evidence fields or failed partial output. Early failures with an empty
payload remain valid; malformed Unicode/status values fail with a bounded
diagnostic. Existing non-Pi Native parsing is unchanged.

Before correction, 28 negative cases were wrongly accepted (three positive
cases passed) in 20.15 seconds. After correction and added actual Native-Pi
success/failure composition cases, all 130 focused result, Native, provider,
protocol and context checks passed in 51.92 seconds; Ruff passed. These are
variants of the missing result boundary, not 28 independently diagnosed
production incidents. This pure validator is deliberately not an authority
port: current persisted assignment/lease admission, pre-reserved registry
evidence and atomic publication remain the next PI-T05 work.

A real `TaskDB` regression also exposed missing tenant persistence for Native
tasks without a ContextBundle: the signed command carried `tenant-1`, while
the Task column remained `None`. The shared queue projection now always
persists the command's explicit tenant. No project, parent, plan or other
relational identity is inferred from runtime IDs. Existing explicit context
preparation still supplies the validated complete project/organization scope.
The regression failed before correction in 7.77 seconds; all 91 focused
preparation, Native adapter, real Pi context and result checks then passed in
40.28 seconds. This is one persistence defect, not new evidence admission.

The route-envelope prerequisite now validates the actual existing Worker
formatter, not a hand-invented success shape. Route status/exit/summary,
workflow adapter result, Native result and its duplicated verification must
agree. Unexpected artifacts/sources, extra fields, another model/assignment
or contradictory nested output fail closed. A framework marker is not copied
into the candidate. Accepted candidates retain canonical JSON strings and
return fresh DTO views, so later mutation of the response or a view cannot
change the validated snapshot. This remains a facts-only helper pending Hub
transactional admission, not a new Worker authority.

All 74 envelope, strict-result and existing workflow-consumer tests passed in
34.52 seconds, including actual Native-Pi success and malformed-protocol
failure through the Worker route formatter. Ruff passed; no live provider,
GPU or public service was contacted.

### Transactional admission implementation boundary

The existing `TaskCompletionPolicyPort.apply` already runs inside Task
repository save/CAS transactions. Extend that composition with a separate
Pi policy, preserving the existing Organization policy. The Pi policy must
validate the persisted command and canonical candidate, lock/read current
ownership, assignment, registration and authorization grant in the same
transaction, and preserve an immutable accepted-result receipt. It must not
mint source/run identities or treat a valid technical result as release proof.
The later registry reservation/result lifecycle remains explicit work.

PostgreSQL can lock those rows until Task commit. SQLite currently serializes
workflow stores using one per-engine lock, but Task writes use only per-Task
locks and legacy deferred transactions. Reuse that existing engine lock and
an immediate SQLite write transaction for the relevant repository save/CAS
boundary; otherwise a lease change could interleave with a successful Task
commit. Validate this with bounded concurrent database tests, then exercise
the full existing forwarding-to-repository path. No replacement task store or
Worker-owned admission loop is needed.

The SQLite prerequisite is now implemented in `task_repository_session`:
Task reads and writes share the existing workflow-store engine guard, and
Task mutations begin an immediate SQLite transaction before reading their
authority snapshot. PostgreSQL retains its existing transaction/row-lock
path. Commit/rollback ownership stays with the repository; this does not
claim to serialize unrelated raw sessions or every repository in the system.

Before correction, four concurrent save/CAS cases failed and the rollback
case passed (13.62 seconds). These reproduced missing writer exclusion and
missing coordination with workflow stores. After correction, 113 focused
Task, queue, context-index, Organization completion and workflow-store tests
passed in 53.68 seconds. The final seven transaction tests, including read
coordination and the non-SQLite branch, passed in 16.82 seconds. These runs
overlap and are synthetic technical observations, not registered release
evidence. Ruff passed.

The expanded run separately exposed two xdist tests sharing and deleting
the same JSON counter file. Their fixture now uses a per-test temporary path;
thread joins have a shared deadline and report failures automatically. That
test-only correction was committed separately. No JSON persistence behavior
was changed. Existing broad Task repository responsibilities remain SRP
debt; transaction mechanics are isolated in a small infrastructure adapter.

### Pi Task admission and polling

Pi completion now composes after the existing Organization completion policy.
The domain policy depends on a narrow current-authority port; its SQL adapter
uses the caller's Task transaction, never an independently opened service
session. Ownership and its JSON/scalar projections, assignment, registered
Worker capabilities/provenance, signed authorization and the exact persisted
grant must agree. PostgreSQL locks the authority rows; SQLite uses the shared
writer boundary above. The original forwarding envelope is checked before
generic projection, then persisted facts are checked again under the write
transaction. A stale dispatch projection cannot select another Worker.

An accepted terminal receives a closed, content-free
`ananta.pi-native-result-receipt.v1` in Task verification. It binds command,
result, tenant/project, Worker, assignment and ownership/grant revisions.
Only exact replay preserves it; it does not renew an expired authorization.
Result/scope/command replacement, reopening a terminal Task and post-hoc
promotion of an unreceipted completion fail closed. Repository instances
without the composed policy cannot terminalize Pi. Hub-owned bounded
dispatch failures/cancellation remain possible without claiming Worker
success. Worker terminal responses still require the complete result contract.

Native polling now requires the same receipt and canonical Task projection;
an otherwise well-formed Worker result alone is no longer accepted. Receipts
are explicitly `technical_observation`, not `SRC_*`/`RUN_*` identities. The
Hub registry reservation before dispatch and result-evidence lifecycle remain
PI-T05 work. The provider stays disabled by default and no production gate is
relaxed.

SOLID review: Task/forwarding services retain their pre-existing broad SRP
responsibilities; this change adds only composition calls there. Command and
result projections, receipt validation, domain policy and SQL authority are
separate small modules. The authority port has one operation and does not
expose scheduling, mutation or registry issuance to Workers. The SQL adapter
reuses existing exact ownership/grant validation through public transaction-
local functions. No new global state, worker-to-worker orchestration, shared
container filesystem requirement or hidden external call is introduced.

Verification: the first integrated admission/forwarding, transaction and
Organization-policy run passed all 72 checks in 42.97 seconds. The final
expanded run passed 285 tests in 115.02 seconds, with one existing skip for
a raw-SQL projection case under the in-memory grant adapter. It includes
Pi save/CAS and original forwarding through actual status persistence,
receipt-required polling, exact replay, tampered receipts, revoked grants,
expired ownership, Worker/assignment changes, actual signature rejection,
Native context preparation and existing authorization/assignment suites.
Ruff, whitespace and Todo consistency checks passed. These are overlapping
synthetic technical checks, not live inference or registered release evidence.

During test development, the fixture initially read an expired ORM object
after closing its session and then used a synthetic future assignment time;
both fixture errors were corrected. The expanded negative test also needed
an explicit JSON dirty marker to persist deliberate `1` versus `true`
corruption. Receipt immutability now compares canonical JSON digests, not
Python's coercive scalar equality, and validates a newly built receipt before
returning it to the repository. The final run covers both direct-write and
persisted-corruption variants.

### Registry lifecycle implementation boundary

At `3aec32991`, Pi has transactional technical-result admission but no
pre-dispatch registry reservation. The existing evidence registry is the
only identity issuer and already exposes source admission, reservation,
closed assignment projection and terminal result recording. Its default
SQL repository opens and commits its own sessions, so invoking it inside a
Task completion policy would break atomicity, particularly on a shared
SQLite connection. Add a repository-port implementation that borrows the
caller's transaction and never commits, rolls back or opens a second session.
Keep row projection/immutable matching shared with the standalone repository;
test rollback and terminal replay against the real registry before wiring it.

Dispatch reservation must follow the actual Hub Worker assignment and precede
transport. It must bind the exact attempt/fencing identity, not a stable
per-step ownership key presented as a unique dispatch lease. The source and
runtime manifest must come from explicit Hub-owned facts and the real
control-Task project scope. No project inference from a workflow ID, arbitrary
hash presented as observed hardware, post-hoc reservation or synthetic-to-
production promotion is permitted. Workers receive only the existing closed
registry projection. These are remaining implementation requirements, not
already completed capabilities.

The caller-transaction repository is now implemented as
`TransactionEvidenceIdentityRepository`. It requires an already active
session, locks current rows for PostgreSQL, flushes without committing and
leaves rollback/retry with the Hub caller. The standalone repository keeps
its API and independent commit behavior, but shares the existing SQLite
engine guard so it cannot interfere with a caller-owned Task transaction.
Both adapters reuse the same row projections and terminal transition rules.
This is an infrastructure prerequisite; Pi dispatch/result lifecycle wiring
is still pending.

All 67 focused registry, transaction, Pi admission and Task-write checks
passed in 45.42 seconds; Ruff passed. The tests exercise source/run/Task
rollback, a registry completion inside the real Task policy seam, exact
terminal replay, closed test-scope projection and production-gate denial.
An earlier 56-test run passed before additional corruption regressions.
Those four new cases reproduced an existing idempotency weakness in
13.19 seconds: changed source content or run environment fields were accepted
when their stored binding digest had not changed. Shared immutable matching
now compares every bound field canonically, excluding only defined lifecycle
timestamps/state/result fields for run replay. The final run covers both
standalone and caller-transaction paths. These are synthetic technical tests,
not a production release claim.

Production Pi submission now resolves the actual Hub control Task even when
no ContextBundle is requested. It inherits the complete validated tenant/
project/organization tuple and parent identity, and requires the corresponding
project to exist and be active. Missing, foreign, malformed or terminal parent
scope and archived/missing projects stop before queue ingestion. If separate
context preparation observes another scope, the projections cannot be merged.
The command remains unchanged. The production queue builder always composes
this preparation; portable injected queue-adapter tests can still exercise
facts without composing Hub persistence, but confer no evidence authority.

The existing ContextBundle preparer now shares its unchanged control-scope
validation with this path. All 96 production-scope, Native queue, context and
Pi admission checks passed in 52.63 seconds; Ruff passed. Scope tests use real
Task/Project rows and the production builder with an injected queue boundary.
An initial fixture replaced the global database engine and was correctly
rejected during isolation teardown; the corrected fixture replaces only the
new adapter's database seam. No production database was accessed. Actual
runtime-manifest admission, dispatch reservation and registry completion
wiring remain the next steps.

The shared `PiRuntimeManifest` now defines closed, immutable scheduler facts:
tenant/project, exact Worker, immutable repository/image revision, Node
version, pinned Pi version and the no-tools execution profile, with explicit
evidence scope and synthetic classification. Credentials, mutable image tags,
unsupported versions and synthetic scope broadening are rejected. Canonical
manifest/environment digests are ordinary hashes, not evidence identities.
Parsing a manifest does not attest that runtime; Hub source admission and
Worker verification still have to bind it to an actual deployment. All 27
contract checks passed in 19.21 seconds; Ruff passed. No manifest was installed
and no model, GPU or external provider was invoked by these tests.

## Lokaler Abschluss-Review am 2026-10-10

Die gesamte Pi-Testsammlung wurde erneut headless ausgeführt: 370 Tests
bestanden in 14,69 Sekunden; der Node-Transporttest wurde übersprungen, weil
Node im Test-Environment fehlt. Abgedeckt sind unter anderem Event-Parsing,
Timeout/Abbruch/Overflow, Secret-Redaktion, Read-only-Policy, Session-Scope,
Provider-Bindung, Worker-Konfiguration und transaktionale Resultatannahme.
Diese synthetischen technischen Beobachtungen sind keine Produktions-Evidence.

PI-T05 bleibt offen für die produktive Aufnahme des Runtime-Manifests in das
Hub Evidence Registry, die RUN-Reservierung vor Dispatch und die gebundene
Registry-Abschlussprüfung. Pi-Resultate erlauben derzeit keine Artefakt-Refs;
die generische Native-Artefaktübergabe allein erfüllt diese Pi-Anforderung nicht.
PI-T06 benötigt außerdem explizit aktivierbare Live-Smokes für lokale und
externe Modellendpunkte. Dabei müssen Hub-Zuweisung und Evidence-Policy erhalten
bleiben; ein direkter CLI-Aufruf darf keinen freigegebenen Hub-Lauf vortäuschen.
Docker-Zugriff ist im aktuellen Sandbox-Kontext verweigert. Es wurde weder
deployt noch ein Modellendpunkt aufgerufen. Die nächsten Implementierungsschritte
sind Manifest-Admission, Dispatch-Reservierung und Registry-Abschlussbindung.

Die neue `PiRuntimeManifestAdmissionService` vergleicht das geschlossene
Manifest exakt mit einer unabhängigen `PiRuntimeDeploymentPort`-Projektion
für Tenant, Projekt und registrierten Worker. Erst danach registriert das
vorhandene Hub Evidence Registry eine unveränderliche Quelle mit Manifest-,
Policy- und Scope-Bindung. Der Pi-Service erzeugt keine SRC-/RUN-IDs selbst.
Wiederholte Admission bleibt durch das Registry idempotent. Abweichende
Deployment-Fakten oder eine nicht verfügbare Deployment-Quelle führen vor
Identity-Ausstellung zum Abbruch.

49 Manifest-/Admission-/Registry-Tests mit echter SQL-Persistenz sind bestanden
(3,08 Sekunden). Deployment-Fakten sind in diesen Tests ausdrücklich synthetisch;
die ausgestellten Test-Quellen dürfen keine Produktionsfreigabe erfüllen.
Ein produktiver Adapter muss registrierten Worker, tatsächlich eingesetztes
unveränderliches Image/Revision und beobachtete Versionen unabhängig verbinden.
Dieser Adapter und die Produktions-Komposition sind noch nicht implementiert;
die neue Servicegrenze ist kein vollständiger Deployment-Nachweis. SRP trennt
Deployment-Faktenprüfung von Identity-Ausstellung; DIP/ISP schützt die schmale
Deployment-Schnittstelle. Bestehende breite Worker-Komposition bleibt erhalten.

Vor Wiederverwendung eines aufgenommenen Manifests prüft `require_admitted`
die aktuelle unabhängige Deployment-Projektion erneut und verifiziert die
persistierte Registry-Quelle unter ihrem exakten Binding-Digest. Origin-Typ,
Manifest-Inhalt, Policy, Scope und synthetische Klassifikation müssen exakt
passen. Eine gültige SRC-Identität für ein anderes Dokument ist keine
Pi-Deployment-Autorität; eine inzwischen geänderte Deployment-Projektion
verhindert die Wiederverwendung. Diese reine Prüfung stellt keine neue
Identität aus. 28 Admission-/Registry-Tests bestanden in 3,05 Sekunden, darunter
sechs Prüfungen zur unveränderten Quellenzahl und falschen Bindungen. Produktive
Deployment-Beobachtung und RUN-Reservierungs-Komposition bleiben offen.

`PiRunEvidenceReservationService` ergänzt nun die Hub-Reservierungsgrenze.
Sie lädt und sperrt den tatsächlichen Pi-Task, prüft die aktuelle Pi-Authority
(registrierter Worker, Assignment, Ownership-Lease und Grant) und verlangt
exakte Tenant-/Projekt-/Worker-Bindung zum erneut verifizierten Runtime-Manifest.
Das vorhandene Registry nimmt die konkrete persistierte Worker-Execution-Context
als Task-Input-Quelle auf und reserviert den RUN unter Task, Assignment,
Native-Dispatch-Binding, Manifest-Revision, Input-, Profil- und Environment-Digest.
Die Native-Dispatch-Kennung bezeichnet die vorhandene Native-Lease-Bindung;
sie ist kein Recovery-Dispatch-Token und keine neue Evidence-Identität.

Reservierung ist nur für `created`, `assigned` oder `queued` erlaubt;
`running`, `in_progress` und terminale Tasks werden vor Aufnahme weiterer
Quellen oder RUN-Ausstellung abgewiesen. Wiederholung bleibt Registry-idempotent.
72 Reservation-/Admission-/Resultat-/Registry-Tests mit echten SQL-Tasks und
Lease-/Assignment-/Registry-Zeilen bestanden in 6,68 Sekunden. Die Deployment-
Projektion ist synthetisch und die reservierten Identitäten sind Test-Evidence.

Die Servicegrenze ist noch nicht in den produktiven Versand eingehängt. Der
Dispatcher muss sie vor jeglicher Worker-Ausführung aufrufen, die geschlossene
Registry-Assignment-Projektion weiterreichen und den Abschluss daran prüfen.
Manifest-Revision und Kontextdigest ersetzen außerdem keinen fehlenden
immutablen Task-Workspace-/Quellennachweis. Registry-Persistenz verwendet ihre
eigene Transaktion; atomare Task-/Outbox-/Registry-Dispatch-Komposition ist
hiermit nicht behauptet. SRP trennt Reservierung von Ausführung und
Resultatprüfung; bestehende SQL-Authority wird über einen injizierten Port
wiederverwendet (DIP). Kein Worker erhält Identity-Ausstellungsbefugnisse.

Die Vorbereitung der geschlossenen Worker-Projektion deckte eine Lücke im
gemeinsamen Registry auf: `assignment_projection` prüfte Task/Assignment/Lease,
berechnete jedoch die persistierte RUN-Bindung nicht erneut. Das Registry
verifiziert jetzt vor jeder Projektion den kanonischen RUN-Digest und die
Hub-Issuer-ID-Bindung. Anschließend müssen alle SRC-Einträge vorhanden,
aufgenommen, unverändert und unter dem Run-Scope zulässig sein. Manipulierte
Inputs, Revisionen, Environment-Fakten, Quellenlisten oder Scope-Felder sowie
veränderte/zurückgezogene Quellen können nicht mehr in eine Worker-Projektion
gelangen. Die kanonische RUN-Digestbildung wird mit der bestehenden abgeschlossenen
Run-Verifikation geteilt, statt zwei Binding-Definitionen zu pflegen (SRP/DRY).
47 Registry-/Pi-Admission-/Reservation-Tests bestanden in 4,29 Sekunden;
Ruff und Diff-Prüfung bestanden. Die produktive Pi-Versand-Komposition bleibt
offen; diese Änderung härtet deren gemeinsame Identity-Grenze.

Die neue Pi-`assignment_projection` lädt vor Weitergabe erneut den gesperrten
Task, verlangt einen Status vor Ausführung und prüft aktuelle Pi-Authority,
frische Deployment-Fakten sowie die exakt aufgenommene Manifestquelle. Das
Registry vergleicht zusätzlich den aktuellen Task-Kontextdigest und den
erwarteten RUN-Binding-Digest. Die verifizierte Manifest-SRC muss zu den im RUN
reservierten Quellen gehören; ein später unter anderer Policy aufgenommener
Manifest-Eintrag darf sie nicht ersetzen. Nur die vorhandene geschlossene
Registry-Projektion wird zurückgegeben. Kontext, Manifest und Environment-Daten
werden nicht zusätzlich an den Worker transportiert. Die neuen Registry-
Parameter sind optional und erhalten bestehende Verbraucher.

39 Pi-Reservation-/Registry-Prüfungen bestanden in 4,78 Sekunden, darunter
Kontextänderung, Lease-Ablauf, bereits gestarteter Task, falscher RUN-Digest,
geänderte Deployment-Beobachtung und ausgetauschte Manifestquelle. Ruff und
Diff-Prüfung bestanden. Dies ist die geprüfte Dispatch-Servicegrenze; sie ist
noch kein Nachweis eines produktiven Worker-Versands oder Registry-Abschlusses.
SRP hält die Dispatch-Prüfung beim Hub; die Worker-Projektion bleibt klein
(ISP) und setzt keine neue Ausstellungsbefugnis voraus.

`PiRunEvidenceCompletionService` bindet jetzt den Registry-Abschluss an ein
bereits persistiertes, vom Hub akzeptiertes Pi-Ergebnis. Sie lädt den gesperrten
terminalen Task, prüft den vollständigen Pi-Umschlag und seine unveränderliche
technische Quittung und vergleicht deren historisch akzeptierte Assignment-
Authority mit der vorreservierten geschlossenen Registry-Projektion. Task,
Lease-Digest, Quellen, aktueller Inputdigest und RUN-Binding müssen exakt passen.
Erst danach wird das Registry-Ergebnis gespeichert; der Worker stellt nichts aus.

Eine exakte Wiederholung nach erfolgreichem Registry-Abschluss prüft die
persistierte terminale RUN-Bindung und alle SRC-Einträge erneut. Lease-Ablauf
nach bereits rechtzeitig akzeptiertem Task-Ergebnis verhindert diese reine
Wiederholung nicht. Fehlende Pi-Quittung, geänderter Kontext, fremde Task-/RUN-
Bindung und ausgetauschte Quellen führen zum Abbruch. Der bestehende Registry-
Resultat-Lookup unterstützt nun optional den erwarteten terminalen Zustand;
der Standard bleibt `succeeded` und ist kein erweitertes Release-Gate.

Der volle Pi-/Registry-Durchlauf bestand 430 Tests mit einem übersprungenen
Node-Transporttest in 16,59 Sekunden. Die danach ergänzte Prüfung gegen
Quellenmutation bei Wiederholung wurde separat in der sieben Fälle umfassenden
Completion-Suite geprüft. Alle verwendeten Deployment-Fakten bleiben synthetisch.
Dispatch- und Abschluss-Services sind noch nicht produktiv eingehängt; atomare
Task-/Registry-/Outbox-Persistenz und tatsächliche Pi-Ausführung unter dieser
Evidence-Projektion sind weiterhin offen. SRP trennt Abschluss von Reservierung
und bestehender Task-Resultatannahme. Bestehende breite Registry-/Runtime-
Komposition bleibt erhalten, ohne Worker-Orchestrierung hinzuzufügen.

### Explizite technische Pi-Live-Smokes

`tests/pi/test_pi_live_smoke.py` enthält getrennte lokale und externe, vollständig headless SDK-Smokes. Standardmäßig sind beide aus. `ANANTA_TEST_PI_LIVE_LOCAL=1` beziehungsweise `ANANTA_TEST_PI_LIVE_EXTERNAL=1` aktiviert genau den ausgewählten Fall. Erforderlich sind jeweils die gleichnamigen Suffixe `_BASE_URL`, `_MODEL` und `_API_KEY`, außerdem `ANANTA_TEST_NODE_BINARY` und `ANANTA_TEST_PI_SDK` (gepinntes `dist/index.js`). Der lokale Fall verwendet den vorhandenen Ollama/OpenAI-kompatiblen Vertrag, der externe den bestehenden OpenRouter-Vertrag mit HTTPS. Fehlende Ressourcen/Auth und ungültige Auswahl schlagen begrenzt fehl; es gibt keine Discovery, Modellwahl, Auth-Abfrage oder menschliche Freigabe.

Der Aufruf nutzt den vorhandenen gepinnten SDK-Einstieg, ein privates No-Tools-/Read-only-Projekt und die Worker-Prozessisolation mit einem eigenen privaten Ergebnisstore-Canary. 32 Ausgabetokens, 30 Sekunden Prozesszeit, 45 Sekunden Testzeit und 64 KiB Ausgabe begrenzen den Lauf. Der bestehende Parser verlangt exakt einen abgeschlossenen Turn mit gebundenem Provider/Modell und ohne Tools oder Retry. Antworten und Credentials werden nicht als Testdiagnose ausgegeben. Aufruf: `python -m pytest -q tests/pi/test_pi_live_smoke.py` mit der expliziten Operator-Auswahl.

Dies sind technische Verbindungs-/SDK-Beobachtungen, keine Hub-Dispatch- oder Produktions-Release-Evidence. Das Testgerüst erzeugt keine SRC-/RUN-Identitäten und darf keine produktive Registry-Abnahme ersetzen. PI-T05 und produktive Manifest-/Provider-/Deployment-Akzeptanz bleiben separat. SRP trennt Auswahl von Ausführung; DIP verwendet die vorhandenen Prozess-, Konfigurations- und Ereignisports. Kein produktiver Default oder Hub-Worker-Orchestrierungsweg wird geändert.

Die zusätzlichen Smoke-Prozessporttests prüften einen erfolgreichen technischen Turn sowie Prozessfehler, Modellsubstitution, unerlaubtes Tool-Ereignis und privaten Canary-Schreibversuch. Der erste Negativlauf fand, dass Pytest bei einer Resultat-Assertion die komplette Objekt-Repräsentation mit synthetischem stderr-Token drucken konnte. Deshalb wird Prozessfehler jetzt ausschließlich als festes ValueError gemeldet; private Provider-Ausgabe wird nicht Teil der Diagnose. Jeder Fehler erzeugt keine Erfolgsbeobachtung und entfernt die eigene Konfiguration. Abschließende85Tests bestehen in4.23s;4explizite Ressourcen-/Live-Smoke-Skips, Ruff/Todo grün. Kein echter Modellendpunkt wurde dabei aufgerufen.

### Signierte Evidence-Transportprojektion als Dispatch-Voraussetzung

Der bestehende Native-Execution-Vertrag unterstützt optional `evidence_assignment` ausschließlich für `pi_coding_agent`. Die geschlossene Hub-Registry-Projektion muss dieselbe Task-ID tragen und ist Teil derselben Ed25519-Signatur einschließlich Worker-Empfänger und Ablauf. Sie wird außerhalb des Task-Inputs transportiert: `worker_execution_context` und sein reservierter Digest bleiben unverändert. Der Vertrag prüft Form und Transportbindung; die produktive Hub-Versandstelle muss die tatsächliche Registry-Reservation und unabhängige Deployment-Fakten weiterhin vorher validieren. Ein Signaturaufruf ersetzt keine Registry-Aufnahme.

`verify_native_execution_envelope` liefert Task und Projektion getrennt. Der bisherige Task-only-Verifier bleibt für alte Requests unverändert; bei vorhandener Evidence weist er begrenzt zurück, damit niemand eine zugewiesene Identität stillschweigend verwirft. Der Worker-HTTP-Port benötigt dafür explizit `evidence_consumer`. Fehlt dieser, erfolgt HTTP503 vor Resultstore-Reservierung und vor jeglicher Ausführung. Ein Legacy-Consumer wird bei einer Evidence-Zuweisung nie aufgerufen. Worker erzeugen keine Identitäten und erhalten keine neuen Orchestrierungsrechte.

35 Registry-/Signatur-/HTTP-Tests bestehen in3.90s. Positive Transporttests verwenden echte vom Hub-Test-Registry ausgestellte SRC/RUN-Identitäten; fremde Task, Nicht-Pi-Profil, umsignierte Projektion, entfernte/null Evidence und fehlender Consumer werden abgewiesen. Das ist eine Transportvoraussetzung; produktive Registry-/Outbox-/Abschluss-Komposition bleibt offen. SRP trennt die geschlossene Projektion vom Task-Input, DIP ergänzt einen kleinen expliziten Consumer-Port und die bisherige API bleibt kompatibel.

### Dauerhafte Ergebnisbindung der signierten Pi-Zuweisung

Der Worker bindet eine über den signierten Native-Transport empfangene
Evidence-Zuweisung zusätzlich an seinen bestehenden SQLite-Ergebnisslot.
Reservierung, Veröffentlichung und Abholung verwenden denselben Digest aus
Task-Projektion und vollständiger geschlossener Hub-Zuweisung. Das unveränderte
`worker_execution_context` bleibt Modellinput; die Zuweisung bleibt separate
Transportautorität. Alte Slots ohne Evidence behalten ihre bisherigen Digests.

Eine andere Assignment-/Dispatch-Bindung oder das Weglassen der Evidence darf
einen bestehenden Slot weder veröffentlichen noch lesen oder erneut ausführen.
Das gilt nach Wiederöffnung der Datenbank und auch bei einer neu gültig
signierten Anfrage. Die Tests verwenden vom Test-Hub registrierte Identitäten;
veränderte Projektionen sind ausschließlich negative Prüfinputs.

SRP/DIP: Die HTTP-Schicht reicht die verifizierte Zuweisung weiter, der lokale
Store vollstreckt die dauerhafte Bindung. Er besitzt weiterhin keine Hub-Queue
und stellt keine Evidence-Identitäten aus. Die produktive Pi-Komposition mit
Manifestzulassung, atomarer Hub-Reservierung/Dispatch und Registry-Abschluss
bleibt offen; diese lokale Prüfung ist keine produktive Release-Evidenz.

### Gemeinsame Hub-Transaktion für RUN und Versandmarkierung

`SQLAlchemyNativeDispatchJournal.begin_in_transaction` verwendet die aktive
Session des Aufrufers. Es lädt die autoritative Task-Zeile unter Sperre,
vergleicht die aktuelle Versandprojektion und schreibt die bestehende eindeutige
Versandmarkierung mit `flush`. Es öffnet keine weitere Session und führt weder
Commit noch Rollback aus. Der bisherige `begin`-Aufruf besitzt weiterhin seine
eigene Transaktion und nutzt dieselbe Prüfung.

Der Hub kann damit das `TransactionEvidenceIdentityRepository`, Pi-Reservierung,
geschlossene Assignment-Projektion, Journal und Task-Status in einem Commit
zusammenführen. Bei einem Journal-Konflikt muss die gesamte Transaktion zurück;
eine bereits erzeugte RUN-/Input-SRC-Reservierung darf nicht separat bestehen
bleiben. Der Versand darf erst nach erfolgreichem Commit beginnen. SQLite-
Serialisierung und Transaktions-Lebensdauer bleiben Verantwortung des Aufrufers.

`tests/pi/test_pi_dispatch_transaction.py` prüft mit echten SQL-Task-, Registry-
und Journal-Zeilen Commit, vollständigen Rollback, bestehende Versandmarkierung,
fehlende aktive Transaktion und veralteten Task-Input. Deployment-Fakten sind
explizit synthetisch. Diese Schnittstelle beseitigt die getrennte Journal-
Transaktion als Kompositionshindernis; sie bindet die produktive Versandroute
noch nicht an einen unabhängigen Deployment-Adapter an.

SRP/DIP: Das Journal behält die Verantwortung für den Versand-Fence, während
der Aufrufer die gemeinsame Transaktion besitzt. Es gibt keine Worker-
Orchestrierung, keine neue Evidence-Ausgabe und keine implizite zweite Session.

### Atomare Pi-Versandvorbereitung

`PiDispatchPreparationService` komponiert nun die tatsächlichen Hub-Services
innerhalb einer gemeinsamen SQL-Transaktion: aktuelle Task-/Assignment-Authority,
unabhängiges Deployment-Port, Manifestaufnahme, RUN-Reservierung, geschlossene
Evidence-Projektion, Ed25519-Transportsignatur, Versandjournal und Task-Status.
Das Deployment und der Policy-Digest kommen aus injizierten Hub-Ports; der
Aufrufer kann keine Manifestbytes als Deployment-Nachweis übergeben.

Die Anfrage wird erst nach erfolgreichem Commit zurückgegeben. Fehler bei
Authority, Signatur, Journal oder Commit rollen Task, neue Registry-Identitäten
und Versandmarkierung zurück. Der Service sendet selbst nichts und besitzt
keine Worker-Ausführung. Die Worker-Evidence bleibt außerhalb des Task-Inputs.
Die Tests trennen die Workflow-HMAC-Testauthority von der zwingend benötigten
Ed25519-Transportsignatur.

SRP/DIP: Dieser kleine Hub-Kompositionsservice besitzt die Transaktionsgrenze;
Registry und Journal behalten ihre Fachverantwortung. Der bestehende direkte
Task-Statuswechsel wird hier transaktional zusammengeführt; die produktive
Routenintegration mit ihren weiteren Policy-/Trace-Hooks ist noch offen.
Insbesondere ist kein unabhängiger produktiver Deployment-Adapter vorhanden.
Die synthetische SQL-Prüfung ersetzt diese Zulassung nicht.

### Native-Autopilot nutzt Pi-Evidence-Ports

Der tatsächliche Native-Versandhandler verlangt für Pi jetzt sowohl
`prepare_pi` als auch `complete_pi`. Er nutzt die atomar vorbereitete signierte
Anfrage und den bereits persistierten Journal-Claim. Die allgemeine
Task-only-Signierung und eine zweite Journal-Reservierung laufen für Pi nicht.
Nach der vorhandenen Hub-Resultatannahme schließt der Pi-Port den reservierten
Registry-RUN; erst danach wird das Journal angenommen.

Das Journal validiert bei Pi die exakte bestehende Pi-Resultatquittung anstelle
der allgemeinen Native-Quittung. Registry- oder Journal-Abschlussfehler werden
als begrenzter fehlgeschlagener Dispatch gemeldet. Eine bereits terminale Task
bleibt dabei terminal; sie ersetzt keinen fehlenden Registry-/Journal-Abschluss.
Ausstehende Versandmarkierungen verhindern erneuten blinden Versand.

Die produktiven Default-Ports besitzen weiterhin keinen unabhängigen
Deployment-Adapter. Deshalb werden Pi-Dispatches ohne installierte vollständige
Hub-Evidence-Komposition vor dem Transport zurückgewiesen. Gewöhnliche
Native-Aufgaben behalten ihren bisherigen Weg. Die Tests installieren die
konkrete atomare Vorbereitung und den Registry-Abschluss mit realen SQL-Zeilen;
Worker-Transport und Deployment-Fakten sind ausdrücklich synthetisch.

SRP/DIP: Zwei kleine Hub-Ports halten Reservierung und Evidence-Abschluss aus
der Route heraus. Die bestehende Route ist weiterhin ein größerer
Orchestrierungsbaustein; diese SRP-Schuld bleibt bestehen. Produktiver
Deployment-Adapter, Evidence-aware Worker-Ausführung und persistente
Evidence-Zuordnung für Recovery bleiben offen.

### Pi-Recovery darf den Registry-Abschluss nicht überspringen

Beide Hub-Recovery-Wege verlangen bei Pi jetzt einen expliziten
Evidence-Abschluss-Port vor der Annahme des Journals. Die reine
`reconcile_native_dispatches`-Prüfung darf eine gültige terminale
Pi-Task-Quittung ohne Registry-Abschluss nicht als angenommen melden.
`recover_native_dispatch_results` benötigt zusätzlich den Port für die
bereits reservierte Assignment-Projektion und bindet sie in die signierte
Ergebnisabholung ein. Der Ergebnisport führt keinen neuen Execute-Aufruf aus.

Ohne vollständige Pi-Evidence-Komposition bleibt der Zustand begrenzt
`unresolved`: RUN und Versandmarkierung bleiben erhalten, die Task wird nicht
umgeschrieben. Nach konkretem Registry-Abschluss kann die vorhandene
Pi-Quittung das Journal schließen. Die realen SQL-Regressionen prüfen beide
fehlenden Kompositionen sowie den erfolgreichen Registry-vor-Journal-Abschluss.

SRP/DIP: Recovery orchestriert die Hub-Ports, Registry prüft die Evidenz und
Journal prüft den Versandabschluss. Eine durable Zuordnung der reservierten
Projektion für neu gestartete Hub-Prozesse bleibt zu implementieren; die
Tests verwenden hierfür eine explizite lokale Port-Komposition.

### Dauerhafte Pi-Evidence-Zuordnung für Hub-Neustarts

Die additive Tabelle `pi_dispatch_evidence` speichert nur die geschlossene
Registry-Assignment-Projektion, Tenant/Projekt/Task und den bestehenden
Journal-Claim. Sie wird im gleichen Commit wie RUN, Journal und Task-Start
geschrieben. Sie enthält keinen Prompt, kein Modellresultat und kein
Worker-Credential. Die vorhandenen Tabellen und APIs werden nicht umbenannt.

`reserved_assignment` lädt die Zuordnung unter Task-/Journal-Sperren und
prüft aktuellen Versand-Input, Claim und Projekt. Danach werden aktuelle
Hub-Authority und die komplette geschlossene Registry-Projektion erneut
verglichen. Der Aufruf reserviert keine neue Identität.
`complete_reserved` nutzt dieselbe dauerhafte Zuordnung für den vorhandenen
strengen Pi-Registry-Abschluss; exakter terminaler Replay bleibt auch nach
Lease-Ablauf möglich. Mutierte Registry-Quellen bleiben dabei gesperrt.

Ein unabhängiger neu geöffneter SQL-Engine-Test lädt den vorhandenen RUN und
schließt ihn ohne zusätzliche Reservierung ab. Drei negative Fälle ändern
Claim, Projekt oder Dispatch-Projektion. Ein Commitfehler entfernt auch die
neue Zuordnungszeile.

SRP: Die Caller-Session-Repositoryklasse persistiert und prüft die lokale
Zuordnung; der Hub-Service revalidiert Registry und aktuelle Authority. Der
Repositoryadapter nutzt weiterhin den vorhandenen reinen Versand-Digest-Helper
aus dem Service-Namespace; diese Namespace-Kopplung bleibt bestehen, ohne
Service-Locator, Hub-Global-State oder zweite Transaktion einzuführen.
Die produktive Installation der Recovery-Ports, der unabhängige Deployment-
Adapter und die Evidence-aware Worker-Ausführung bleiben offen.

### Anwendungsspezifische Pi-Dispatch-/Recovery-Komposition

`install_pi_dispatch_service(app, service=...)` installiert die vollständige
Hub-Pi-Komposition nur für diese Anwendung. Worker-Rolle, unvollständiger
Service und doppelte Installation werden zurückgewiesen. Es gibt keinen
globalen Pi-Service und keine Übernahme zwischen Flask-Anwendungen.

Die tatsächliche Native-Versandroute nutzt den installierten Service, wenn
keine expliziten Pi-Test-/Override-Ports gesetzt sind. Teilweise gesetzte
Override-Ports werden nicht mit einer anderen Komposition gemischt.
`production_native_result_recovery_ports` übernimmt aus derselben Anwendung
`reserved_assignment` und `complete_reserved`. Die gewöhnlichen
Native-Ausführungen behalten ihre bestehenden Ports.

Die Produktionsfactory wird im Test mit dem tatsächlichen installierten
Service und neu geöffnetem SQL-Engine ausgeführt: der bestehende terminale
Pi-RUN und das Journal werden angenommen, ohne Fetch oder Execute. Eine
zweite Routenprüfung nutzt direkt die App-Installation statt der expliziten
Pi-Override-Ports. Deployment und Inferenz bleiben dabei synthetisch.

DIP/SRP: Die Anwendung besitzt die Komposition; Route und Recovery greifen
auf die jeweils benötigten Lifecycle-Methoden zu. Die vorhandene Route-Seam
und die explizite Hub-Rolle bleiben erhalten. Die automatische produktive
Erstellung dieses Services aus unabhängigen Deployment-/Policy-Fakten sowie
die Evidence-aware Worker-Ausführung bleiben offen. Eine manuelle interne
Testinstallation ist keine Deployment-Zulassung.

### Worker-Consumer mit aktueller Hub-Evidence-Prüfung

`VerifiedPiEvidenceConsumer` kann als expliziter `evidence_consumer` des
signaturprüfenden Native-HTTP-Ports eingesetzt werden. Er validiert Pi-Task,
Native-Kommando, Tenant und geschlossene Task-Evidence-Bindung und liest vor
der Ausführung die aktuelle Registry-Projektion über `PiEvidenceReadPort`.
Nur die vollständige kanonisch identische Projektion erlaubt den bestehenden
Ausführungs-Consumer. Hub-Ausfall oder geänderte Assignment-/Dispatch-Bindung
führt zu einem begrenzten Fehler ohne Pi-Ausführung.

Der Decision-Port erhält separate Kopien von Task und Kommando; er kann
den späteren Modellinput nicht verändern. Worker stellen weiterhin keine
Identitäten aus und treffen keine Queue-/Delegationsentscheidung.

Die Tests nutzen echte vom Test-Hub registrierte positive SRC/RUN-Identitäten.
Zusätzlich verbindet ein HTTP-Test Signaturprüfung, Consumer und tatsächliches
Test-Registry: eine nach der Signierung mutierte SQL-Quelle sperrt die Ausführung.
Der Registry-Aufruf erfolgt ausschließlich über einen injizierten Test-Port,
nicht als Hub-Repository-Import in produktivem Worker-Code.

SRP/ISP/DIP: Der kleine Reader-Port prüft ausschließlich die aktuelle
Evidence-Zuweisung; Ausführung und HTTP-Signaturprüfung bleiben getrennt.
Der authentifizierte produktive Worker-Hub-Transport dieses Readers und seine
Runtime-Komposition sind noch zu implementieren. Der HTTP-Default bleibt
ohne vollständig zusammengesetzten Evidence-Consumer gesperrt.

### Authentifizierter Pi-Evidence-Leseweg über Workflow-Gateway

Der additive `pi_evidence_read`-Befehl nutzt denselben authentifizierten
Workflow-Worker-Transport. Er verlangt explizit registrierte Worker-ID und URL,
aktuelle Ownership/Fencing und die Task-ID der exakten Worker-Assignment.
Anschließend prüft die bestehende Hub-Authority die signierte Workflow-Bindung.
Erst danach wird der Pi-Leseport aufgerufen.

Die produktive Gateway-Komposition enthält einen anwendungsbezogenen Callback
zum installierten Pi-Dispatch-Service. Dieser vergleicht aktuelle Task-/Input-/
Kommando-Bindung, Worker-ID/URL und Tenant/Workflow/Run/Step mit den zuvor
authentifizierten Gateway-Fakten und lädt nur die bereits reservierte
Registry-Projektion. Ohne installierten Pi-Service bleibt der Aufruf gesperrt.
Der Callback erzeugt weder Identitäten noch Tasks.

`HubPiEvidenceReader` sendet das signierte Workflow-Envelope, Task-/Kommando-ID,
Attempt/Fencing und tatsächlichen Input-Digest über den bestehenden
`HttpWorkflowHubDecisionClient`. Er sendet keine selbst gewählte RUN-/SRC-ID
und gibt nur eine validierte geschlossene Projektion an den Consumer weiter.

Die Tests prüfen registrierten Eigentümer, anonymen/fremden Worker, fremde
Task, stale Fence, fehlenden Hub-Port, tatsächlichen Task-Workflow-Abgleich
und die geschlossene Worker-Anfrage. Positive Registry-Identitäten kommen aus
dem Test-Hub; der Gateway-Auth-Test isoliert nur den Decision-Callback und
behauptet keine Registry- oder produktive Evidence.

SRP/DIP: Reader und App-Callback sind getrennte kleine Adapter. Der vorhandene
Gateway-Dispatcher bleibt ein größerer, zentraler Kompositionsbaustein; diese
SRP-/OCP-Schuld wird mit einem additiven Befehl fortgeführt. Die globale
Gateway-Service-Cache-Struktur bleibt bestehen, während der Pi-Service bei
jedem Aufruf aus der jeweiligen Hub-Anwendung aufgelöst wird. Runtime-
Installation des Worker-Consumers und unabhängige Deployment-Zulassung
bleiben offen.

### Worker-HTTP-Runtime installiert Evidence-Consumer

Die produktive Worker-Blueprint-Registrierung installiert jetzt
`consume_pi_evidence_task` als expliziten Evidence-Consumer. Pro Anfrage
komponiert er den bestehenden authentifizierten Hub-Decision-Client,
`HubPiEvidenceReader`, die genaue Zuweisungsprüfung und den vorhandenen
Workflow-Ausführungsconsumer. Fehlende Transportkonfiguration oder Hub-Ausfall
verhindern die Ausführung; eine Task-only-Ausführung ist kein Fallback.
Gewöhnliche Native-Anfragen behalten ihren bisherigen Consumer.

SRP/DIP: Die neue Worker-Komposition führt ausschließlich delegierte Arbeit
aus und bleibt getrennt von Hub-Registry und Task-Queue. Drei Runtime-Fälle
prüfen den erfolgreichen aktuellen Read sowie fehlenden/ausgefallenen Hub
vor Ausführung. Die unabhängige Hub-Deployment-Zulassung und produktive
Live-Abnahme bleiben offen.

### Explizite Hub-Startup-Komposition

Die App startet jetzt die Phase `pi_dispatch_evidence`. Der Bootstrap ist
standardmäßig deaktiviert. Ein explizites boolesches
`ANANTA_PI_EVIDENCE_ENABLED=True` verlangt die Hub-Rolle sowie unabhängige
Deployment- und Policy-Ports in den Hub-App-Extensions
`ananta.pi_runtime_deployments` und `ananta.pi_evidence_policy`.
Der Policy-Port bietet `digest_for_task`, der Deployment-Port
`require_manifest`. Diese Ports werden ausschließlich durch vertrauenswürdige
Hub-Bootstrap-Komposition installiert; Worker-Metadaten oder Task-Input können
sie nicht ersetzen.

Fehlen Ports, liefert der Bootstrap sofort den maschinenlesbaren Zustand
`blocked` mit `pi_evidence_independent_ports_unavailable`; es gibt kein
interaktives Warten, keine erfundenen Deployment-Fakten und keine Installation
eines unvollständigen Dispatch-Service. Sind die Ports vorhanden, baut die
produktive Factory den SQL-Pi-Service mit Hub-Authority und Signierschlüsseln
und installiert ihn pro Anwendung. `ready` bedeutet ausschließlich
Komposition bereit, nicht Deployment oder Release abgenommen.

Die vorhandene Docker-Übersicht `inspect_light` kürzt Image-IDs und besitzt
keinen unabhängigen Pi-Versionsnachweis; sie ist deshalb keine geeignete
Ersatz-Zulassung. Ein konkreter unabhängiger Deployment-Observer bleibt offen.

SRP/DIP: Der Bootstrap besitzt nur Aktivierung und Port-Komposition;
Manifest-/Registry-Zulassung und Worker-Ausführung bleiben getrennt.
Das Startup wird vor dem Start der Hintergrunddienste zusammengesetzt.
Es werden keine zusätzlichen Docker-Berechtigungen oder produktiven
Deployments eingeführt.

### Konkreter gepinnter Deployment-Observer

`PinnedPiRuntimeDeployments` bindet exakt Tenant/Projekt/Worker-ID/URL an
eine vom Hub freigegebene vollständige Container-ID, unveränderliche
Manifest-Fakten, feste Node-/Package-Pfade und einen nichtprivilegierten
numerischen Benutzer. Unbekannte oder doppelte Zuordnungen werden abgewiesen.
Jeder Zugriff vergleicht eine frische unabhängige Beobachtung mit allen
gepinnten Manifest-Fakten; es gibt keinen Worker-Report- oder Cache-Fallback.

`DockerPiRuntimeObserver` verwendet einen ausdrücklich bereitgestellten
Hub-Docker-Runner. Er fragt nur volle Container-/Image-ID, Running,
ReadonlyRootfs, Start-Marker, Mount-Zielpfade und gepinnte OCI-Revision ab.
Environment, Credentials und Mount-Quellpfade werden nicht gelesen. Feste
Lesekommandos beobachten Node-Version und Package-Name/-Version mit
`env -i`, nichtprivilegiertem Benutzer, isoliertem Python und ohne Shell.
Node/Package/Interpreter-/Bibliotheks-Mounts sowie Symlink-Ziele in
veränderlichen Mounts werden gesperrt. Ein zweiter Containervergleich
verhindert Übernahme bei einem Wechsel während der Prüfung.

Jeder der fünf möglichen Docker-Aufrufe besitzt ein Fünf-Sekunden-Limit
und das Ergebnis wird auf 16 KiB begrenzt geprüft. Runner-Fehler und
abweichende Identitäten/Versionen liefern feste Fehlercodes ohne Rohoutput.
Die Prüfung ergänzt die bestehenden Sandbox-Grenzen, ersetzt aber keine
allgemeine Container-/Netzwerk-/Ressourcen-Zulassung.

SRP/DIP: Gepinnte Hub-Inventarentscheidungen und Docker-Infrastruktur sind
getrennte Adapter; der bestehende Manifest-Admission-Service bleibt
unverändert. Es wird kein Docker-Socket zusätzlich verfügbar gemacht.
Die 19 neuen Tests verwenden synthetische Docker-Ausgaben. Reale
Container-Probe, vertrauenswürdige Inventar-/Policy-Provisionierung und
produktive Live-Abnahme bleiben offen.

### Reale isolierte Docker-Probe des Deployment-Observers

`tests/pi/pi_deployment_observer_live_gate.py` erstellt ausschließlich ein
eigenes temporäres Fixture-Image aus einem lokal gepinnten Basisimage,
dem tatsächlichen Node-Binary und dem vorhandenen gepinnten SDK. Es startet
zwei eigene Container ohne Netzwerk, mit ReadonlyRootfs, ohne Linux-Capabilities,
mit `no-new-privileges` und begrenzten CPUs/PIDs/Memory. Der positive Observer
erkennt tatsächlich Node 24.21.0 und Pi 0.85.1. Ein zweiter Container mit
einer gemounteten SDK-Laufzeit wird vor dem Versionsaufruf abgewiesen.
Die eigenen Container und temporären Image-Referenzen werden entfernt.

Der erste Build scheiterte an der lokalen Basisimage-Auflösung; der Gate
verwendet jetzt eine eigene temporäre Referenz auf das gepinnte Image. Die
erste tatsächliche Observer-Ausführung fand zusätzlich eine abschließende
Docker-Leerzeile, die der Parser nicht akzeptierte. Nur abschließende
Zeilenumbrüche werden jetzt entfernt; ein zusätzlicher Unit-Test deckt die
reale Ausgabeform ab. Inhaltliche Identitäts-/Mount-/Versionsprüfungen
bleiben unverändert. Beide realen Containerfälle bestehen anschließend.

Diese Probe ist eine lokale technische Beobachtung ohne Hub-reservierten
Release-RUN und ohne Modellaufruf. Die Fixture-OCI-Revision ist ausdrücklich
ein Testlabel, kein Nachweis der Source-Provenance des Basisimages. Die
vertrauenswürdige produktive Inventar-/Policy-Zulassung und komplette
Live-Abnahme bleiben offen.

### Gepinnte Hub-Evidence-Policy statt frei gewähltem Digest

`PiEvidencePolicyPin` ist eine unveränderliche Hub-Deklaration für exakt
ein Deployment, eine signierte Policy-Version, ein Providerprofil, einen
Provider und ein Modell. Die explizite `preauthorized_read_only`-Freigabe
ist standardmäßig false. `PinnedPiEvidencePolicy.digest_for_task` liefert
nur nach exakter Task-/Projekt-/Worker-Bindung einen kanonischen Digest
der gesamten Policy. Eine neue Deployment-Beobachtung muss dasselbe
gepinnte Manifest bestätigen.

Das Profil bleibt `pi-sdk-no-tools-v1`/`read_only`: Schreiboperationen,
Ein-/Ausgabeartefakte und fehlende Pi-Capability werden nicht zugelassen.
Policy-/Provider-/Modellwechsel sowie nicht vorautorisierte oder fremde
Aufgaben werden begrenzt abgewiesen. Die Policy ergänzt die vorhandene
aktuelle SQL-Hub-Authority und Organization-/Grant-Prüfungen; sie ersetzt
oder erteilt keine obligatorische Sicherheitsfreigabe.

Der tatsächliche SQL-Reservierungstest prüft, dass beide aufgenommenen
Quellen exakt diesen Policy-Digest tragen. Ein nicht freigegebener Pin
verhindert RUN-Reservierung und Task-Start. Das ursprüngliche negative
Testsetup kopierte ORM-State mit `model_copy` und konnte die Attribute
nicht ändern; es nutzt jetzt den bestehenden Domain-Task-Repository-Port.
Die zugelassenen Fakten sind ausdrücklich synthetische Test-Policy.

SRP/DIP: Policy und Deployment-Observer bleiben getrennt; die Policy hängt
am schmalen `PiRuntimeDeploymentPort`. Worker und Task-Inhalt können
weder den Pin verändern noch die boolesche Vorautorisierung aktivieren.
Trusted Policy-/Inventar-Provisionierung und produktive Live-Abnahme
bleiben offen; keine produktive Aktivierung ist erfolgt.

### Geschlossene Konfiguration provisioniert die Hub-Ports

Die normale vertrauenswürdige Hub-`AGENT_CONFIG` enthält jetzt standardmäßig
`pi_evidence: {enabled: false, bindings: []}`. Bei expliziter Aktivierung
kann der Startup die konkreten Deployment-/Policy-Ports aus `bindings`
erstellen. Er benutzt dafür den begrenzten Docker-Observer, ohne einen
zusätzlichen Socket oder Container-Privilege bereitzustellen. Fehlt der
Zugriff im Hub, scheitert die spätere aktuelle Beobachtung begrenzt.

Jede der höchstens 64 Bindungen enthält genau:

- `manifest`: die vollständige geschlossene `PiRuntimeManifest.to_dict()`-Form,
- `container_id`: volle lokal freigegebene Container-ID,
- `node_binary` und `package_json`: feste absolute Image-Pfade,
- `user_id`: expliziter nichtprivilegierter numerischer Benutzer und Gruppe,
- `policy`: `policy_version`, `provider_profile_id`, `provider_id`, `model_id`
  und optional das strikt boolesche `preauthorized_read_only` (Default false).

Unbekannte Felder, unvollständige Daten, doppelte Scopes, relative Pfade
und String-Freigaben werden vor der Installation abgewiesen. Ein
synthetisches Manifest darf seinen Evidence-Scope nicht auf production
ändern. Es werden keine Credentials in diesem Block benötigt oder akzeptiert.

Sind bereits vollständige vertrauenswürdige Bootstrap-Ports installiert,
werden sie weiterverwendet; teilweise installierte Ports werden nicht mit
anderen Konfigurationsquellen gemischt. Bei deaktiviertem Profil wird kein
Docker-Observer erstellt. `ready` bezeichnet weiter nur die abgeschlossene
Komposition: vor jedem Versand bleiben aktuelle Deployment-, Policy-,
Assignment- und Registry-Prüfungen erforderlich.

Der Integrationstest liest die normale Konfiguration, baut beide konkreten
Ports und führt echte SQL-Hub-Reservierung durch. Die Runtime-Beobachtung
ist dabei explizit synthetisch; die getrennte echte isolierte Docker-Probe
bleibt eine technische Beobachtung. Produktive Konfigurationswerte und
Live-Abnahme sind weiterhin nicht gesetzt bzw. durchgeführt.

SRP/DIP: Der neue Konfigurationsadapter übersetzt ausschließlich die
geschlossene Hub-Deklaration in die bestehenden schmalen Ports. Policy,
Docker-Infrastruktur und Transaktions-Service bleiben getrennt.

### Auditkorrektur: vorbereitete Kontextreferenz im Versand

Der Hub-Queue-Adapter legt vorbereitete `context_bundle_id` am Top-Level
der Task ab. Die Native-Transportprojektion hatte dieses Feld bisher
weggelassen. Der Worker-Context-Reader sah daher keine Referenz und konnte
den vorbereiteten Kontext nicht anfordern. Die optionale Top-Level-Referenz
ist jetzt Teil derselben signierten Task-Projektion und überlebt den
Versand bis zum bestehenden `HubPiTaskContextReader`. Nachträglicher
Referenztausch scheitert an der Signatur.

Fehlende oder null Referenzen werden weiter ausgelassen, damit bestehende
gewöhnliche Task-/Store-/Journal-Projektionen unverändert bleiben. Eine
vorhandene Referenz muss eine begrenzte, nichtleere Zeichenkette ohne
Whitespace-Steuerzeichen sein. Für historische context-bearing Journals
ändert sich die Projektion; ein alter abweichender Journal-Digest darf
nicht durch einen neuen Versand oder eine erfundene Migration ersetzt
werden und bleibt unter der vorhandenen Bindungsprüfung gesperrt.

SRP: Der geschlossene Transportvertrag erhält die Referenz, der vorhandene
Hub-Reader prüft und liefert die genehmigte Projektion. Das ist keine
ContextBundle-Evidence-Zulassung. Der weitere Audit hat gezeigt, dass die
RUN-Reservierung bisher Deployment- und Task-Kontextquelle bindet, aber
keine separate Quelle der tatsächlich genehmigten Bundle-Bytes. Diese
Bindung ist zusätzlich zu implementieren; die vollständige Pi-Umstellung
ist deshalb weiterhin unvollständig.

### Aufnahme genehmigter Kontextbytes vor Pi-RUN-Reservierung

`PiContextEvidenceAdmission` erhält die genehmigte
`NativeContextBundleProjection` über den schmalen
`PiApprovedContextPort.read(session, task, command, worker_id)` in der
bestehenden Hub-Transaktion. Sie prüft Bytes-Digest, Task, Bundle und
Kommando. Erst danach stellt das Hub-Registry eine eigene Quelle des Typs
`pi_native_approved_context` aus. Ihr Policy-Digest bindet sowohl die
Pi-Vorautorisierung als auch die ausgewertete Context-Policy. Scope und
Synthetic-Klassifikation stammen aus dem zugelassenen Manifest.

Die RUN-Reservierung enthält diese Quelle zusätzlich zu Deployment und
Task-Kontext. Die atomare Versandvorbereitung kann denselben Context-Port
komponieren; fehlender Port oder widersprüchliche Projektion führt bei
Kontextaufgaben vor RUN-/Journal-/Task-Start zum Rollback. Bei Aufgaben
ohne Kontext bleiben die bisherigen zwei Quellen unverändert. Eine
deklarierte Context-Policy ohne Referenz wird ausdrücklich abgewiesen.

Die Referenzprüfung wurde als gemeinsamer Contract-Helper vereinheitlicht;
der bisherige Worker-Import bleibt als Re-Export kompatibel.

SRP/DIP: Der neue Admission-Service besitzt nur Prüfung und Aufnahme der
bereits genehmigten Projektion; Source-IDs kommen weiterhin ausschließlich
aus dem Registry. Die acht neuen SQL-Tests verwenden einen explizit
synthetischen Hub-Projektionsport. Die konkrete transaktionsgebundene
Policy-Projektion sowie erneute Prüfung der reservierten Bytes beim
Worker-Abruf und Registry-Abschluss bleiben zu implementieren. Die
Kontextaufgaben bleiben im produktiven Default ohne vollständigen Port
begrenzt gesperrt; dies ist ein Zwischenstand, kein Ersatz für deren
vollständige Umstellung.

### Transaktionsgebundener genehmigter Pi-Kontext

`TransactionPiApprovedContextReader` sperrt die aktuelle Task und ihr
Bundle in der bereits aktiven Caller-Session, prüft Kommando-/Bundle-
Eigentümerbindung und den vorbereiteten Snapshot und verwendet anschließend
die bestehende `NativeContextPolicyService`-Freigabe.
`TransactionActiveContextPolicies` liest ausschließlich die aktuelle
Policy-Version aus derselben Session mit PostgreSQL-Row-Lock; sie öffnet
und beendet keine eigene Transaktion. Der Destination-Katalog bleibt
ein expliziter anwendungsbezogener Port.

Die vorhandene Row→Policy-Domain-Projektion ist jetzt eine gemeinsame
reine Funktion; das bisherige Repository-API delegiert unverändert an sie.
Es gibt keine zweite Implementierung dieser Bindungsfelder.

Der Hub-Startup installiert den Reader in die Pi-Versandvorbereitung,
wenn der bestehende `source_control_destination_catalog` verfügbar ist.
Fehlt er, bleiben Context-Aufgaben unter dem vorhandenen fehlenden
Projektionsport begrenzt gesperrt.

Fünf neue Tests verwenden echte SQL-Task-, Bundle- und aktive Policy-
Zeilen. Sie prüfen freigegebene statt unbewertet zusammengestellte
Bytes, fremden Eigentümer, widerrufene/geänderte Policy, fremdes Kommando
und fehlende aktive Caller-Transaktion. Das erste Testsetup griff auf
eine abgelaufene ORM-Instanz nach Commit zu; es verwendet jetzt die
feste technische Task-ID beim Wiederladen.

SRP/DIP: Aktive Policy-Persistenz und Kontext-Projektion bleiben getrennt
und nutzen den bestehenden Destination-Port. Die bestehende
`ContextAccessPolicyService`-Fassade koppelt reine Block-Prüfung weiterhin
an die Initialisierung ihres globalen Repository-/Klassifikations-
Locators; diese SRP-/DIP-Schuld bleibt erhalten. Eine reine
`NativeContextBlockPolicyPort`-Komposition wäre die sauberere künftige
Struktur. Reservierte Bytes müssen weiterhin beim Worker-Abruf und
Registry-Abschluss exakt revalidiert werden.

### Reservierte Kontextbytes bei Abruf und Abschluss prüfen

Das Registry bietet jetzt `assignment_sources` als reine Prüfung/Abfrage
der bereits registrierten exakten Assignment-Quellen an. RUN-Bindung,
Hub-Identität, geschlossene Projektion und jede Quelle werden revalidiert;
die Abfrage stellt keine neue SRC-/RUN-Identität aus. Sie verwendet die
bestehende gemeinsame Source-Prüfung und liefert deren geprüfte DTOs.

`PiContextEvidenceAdmission.require_reserved` vergleicht die aktuelle
genehmigte Projektion mit exakt einer reservierten Context-Quelle.
Task/Bundle/Kommando-Origin, Bytes-Digest und kombinierte Context-/Pi-Policy
müssen unverändert sein. Für Abschluss und Replay kommt der ursprüngliche
Pi-Policy-Digest aus der bereits aufgenommenen Task-Inputquelle, nicht
aus einer neuen nachträglichen Vorautorisierung.

Der Hub-Assignment-Read und `reserved_context` führen denselben Vergleich
in einer Caller-Transaktion aus. Die produktive Gateway-Komposition
verwendet `reserved_context` für `native_context_read`: die bereits
authentifizierte Task-/Worker-/Workflow-Bindung muss mit dem gespeicherten
RUN übereinstimmen. Sie gibt nur die exakt reservierten genehmigten Bytes
zurück und behält den bestehenden content-freien Bundle-Read-Auditevent.
Explizite technische Gateway-Testkompositionen bleiben kompatibel.

Registry-Abschluss und terminaler Replay prüfen ebenfalls diese Bindung.
Geänderte Bytes, andere Context-Policy oder entfernte Referenz verhindern
Abschluss und lassen den RUN reserviert. Es wird dabei keine Ersatzquelle
aufgenommen. Exakter Replay nach Lease-Ablauf bleibt möglich, sofern
Context-Policy und Quellen weiter gültig sind.

SRP/DIP/LSP: Source-Verifikation bleibt im Registry, Kontextvergleich im
Admission-Service. Assignment-Read und Context-Read haben getrennte
Rückgabeverträge und teilen eine private geprüfte Input-Projektion; keine
Return-Type-Umschaltung im öffentlichen Assignment-Port. Die bestehende
Gateway-Verzweigung und CAP-Fassadenkopplung bleiben als dokumentierte
SRP/OCP-Schuld bestehen. Eine vollständige integrierte SDK-/Provider-
Abnahme dieser neuen Kontextkette ist weiterhin erforderlich.

### Native Worker → Hub-Autorisierung/Budget → echtes gepinntes SDK

`test_pi_native_pinned_sdk.py` führt den tatsächlichen Native-Worker-
Adapter und Pi-Provider mit dem vorhandenen Node-/SDK-Paket, begrenztem
SDK-Prozess und privater Ergebnisdatei aus. Nur die Antwort des eigenen
Loopback-Modellservers ist synthetisch. Der vom Test-Hub ausgewählte
Endpoint ist bereits vor Signierung in Providerprofil, Budget-Context und
Native-Kommando identisch gebunden; der tatsächlich empfangene Request
nutzt exakt diesen Pfad und `selected-model`, ohne Tools.

Die Testkomposition erlaubt dazu explizite Endpoint-/Provider-Optionen.
Bestehende Defaults bleiben erhalten. Die Kommando-Fixture leitet den
Endpoint aus dem echten Hub-Context ab, statt ihren festen Default erneut
einzusetzen. Ein zusätzlicher Konsistenztest prüft diese Binding-Kette.

Der echte SDK-Lauf liefert einen gültigen terminalen Native-Resultat-
Datensatz und lässt die private Canary-Datei unverändert. Die zwei
konkreten Testressourcen sind explizit über `ANANTA_TEST_NODE_BINARY`
und `ANANTA_TEST_PI_SDK` zu setzen; ohne sie wird der Hardware-/Paketpfad
als nicht ausgeführt markiert. Es gibt keinen externen Modellkontakt.

Die Hub-Signatur-/Budget-Stores dieser Worker-Komposition sind die
vorhandenen In-Memory-Testports. Dies beweist den tatsächlichen SDK-Pfad,
aber noch nicht die gemeinsame komplette SQL-Context-/Registry-Dispatch-
Kette. Deren Integration und produktive Provider-/Deployment-Abnahme
bleiben ausdrücklich offen; keine produktive Release-Evidenz.

SRP/DIP: Endpoint-Auswahl bleibt beim Hub, tatsächlicher SDK-Prozess beim
Worker. Die injizierten Provider-Testports ersetzen ausschließlich die
Resource-Auswahl und verbinden den vorhandenen Ausführungsadapter.

### Gemeinsamer SQL-/Kontext-/Worker-HTTP-/SDK-/Registry-Nachweis

`test_pi_sdk_sql_evidence_chain.py` verbindet die tatsächlichen SQL-Task-,
Assignment-/Grant-, Context-Policy-/Bundle- und Evidence-Registry-Zeilen mit
der atomaren Pi-Versandvorbereitung. Der signierte Worker-HTTP-Aufruf geht
durch die Evidence-Prüfung, authentifizierte Hub-Gateway-Entscheidungen und
den Native-Worker-Adapter bis zum echten gepinnten SDK-Prozess.

Der eigene Modellserver beobachtet beim ersten tatsächlichen Request den
bereits reservierten SQL-RUN mit drei Quellen. Der Request enthält nur die
genehmigten Kontextbytes und das vorgegebene Modell, keine Tools. Das echte
SDK-Ergebnis wird durch die vorhandene SQL-Pi-Ergebnisannahme persistiert;
anschließend werden exakt derselbe RUN und das Versandjournal abgeschlossen.
Die private Canary-Datei bleibt unverändert.

Ein zweiter Fall ändert die Bundle-Bytes nach Reservierung. Der signierte
Worker-Aufruf wird vor jedem SDK-Modellrequest abgewiesen, der RUN bleibt
mit den ursprünglichen drei Quellen reserviert. Es gibt kein vorher
erzeugtes erfolgreiches Mock-Resultat für diesen Test.

Grenzen: Der Modellinhalt und die Deployment-Fakten sind synthetisch.
Workflow-Signatur-/Budget-Gateway-Stores verwenden weiter die vorhandenen
In-Memory-Testports; Task-/Lease-/Grant-/Kontext-/Registry-Annahme nutzt
echte SQL-Persistenz. Hub-/Worker-Flask-Adapter laufen im Testprozess,
der SDK-Prozess ist tatsächlich separat und filesystem-isoliert. Dies
beweist die lokale zusammengesetzte Kette, nicht produktive Container-/
Provider-Live-Abnahme. Synthetic-Scope verhindert Release-Promotion.

SRP/DIP: Die neue Prüfung komponiert die vorhandenen getrennten Ports;
es werden keine weiteren Produktionspfade oder Worker-Orchestrierungs-
rechte eingeführt. Eine produktive Freigabe bleibt separat erforderlich.

### Legacy-Worker-Einstiege verlangen aktuelle Pi-Evidence

Der Abschlussaudit fand einen alternativen Einstieg: Der produktive
Native-Adapter konnte auch über den bisherigen Task-only-Consumer
aufgerufen werden. Seine Pi-Node-Komposition prüfte vorher nur
Workflow-Autorisierung und Budget; sie verlangte keinen reservierten RUN.

Die einzige produktive `NativePiNodeHandler`-Erstellung in
`pi_composition` installiert jetzt zwingend `HubPiEvidenceReader`.
Der Node liest vor Credential-Auflösung, Providerbudget und SDK die
aktuelle registrierte Assignment-Projektion und validiert deren genaue
Task-Bindung. Damit hängt die Evidence-Prüfung am produktiven
Ausführungsadapter selbst und wird durch einen Task-only-Einstieg
nicht übersprungen. Der HTTP-Evidence-Consumer bleibt zusätzlich aktiv.

Der vorherige Produktions-Kompositionstest ohne Registry-Port muss
jetzt begrenzt fehlschlagen. Er prüft ausdrücklich: kein Shell-Fallback,
kein SDK-/Provider-Aufruf, kein Credential-Read und kein Providerbudget.
Die gemeinsame SQL-/SDK-Kette installiert denselben Node-Reader und
besteht mit reserviertem RUN weiterhin. Direkte isolierte Node-/Provider-
Testkompositionen können technische Beobachtungen ohne Registry-Port
ausführen; sie sind nicht die produktive Factory und liefern keine
Release-Evidenz.

SRP/DIP: Die Node-Prüfung benutzt einen schmalen Evidence-Leseport und
bleibt execution-only; der Hub entscheidet weiterhin allein über
Registry, Aufgaben und Delegation. Es wird keine Worker-Orchestrierung
oder neue Identitätsausgabe eingeführt. Produktive Container-/Provider-
Abnahme bleibt offen.

### Exakte signierte Zuweisung bis zur Node-Ausführung

Der verifizierte HTTP-Evidence-Consumer bindet jetzt die geschlossene
signierte Assignment-Projektion für die synchrone Ausführung in einem
Task-lokalen ContextVar. Die gespeicherte Form ist ein unveränderlicher
JSON-Snapshot; Leser erhalten eine neue validierte Kopie. Nach Erfolg
oder Fehler wird der vorherige Kontext wiederhergestellt.

Der produktiv mit Evidence-Reader konfigurierte Pi-Node verlangt diesen
Scope und vergleicht die erneute aktuelle Hub-Projektion kanonisch mit
dem exakt signierten Assignment. Er kann kein anderes aktuelles RUN-/
Assignment übernehmen. Task-only-Einstiege ohne signierten Scope werden
jetzt bereits vor Registry-Read, Credentials, Budget und SDK gesperrt.

Neue negative Tests prüfen mutierte Rückgabekopien, fremde Task-ID,
separaten Context, Fehler-Cleanup und geänderte Assignment-Projektion
zwischen Consumer und Node. Die echte SQL-/SDK-Kette funktioniert weiter.

SRP/DIP: Der kleine Scope-Port trägt ausschließlich die bereits geprüfte
Zuweisung, ohne Datenbank, Identitätsausgabe oder Orchestrierung. Das
ContextVar ist wie der vorhandene Native-Ausführungsscope kontextlokal,
kein geteilter Task-Wert zwischen Containern oder Worker-Aufträgen.
Produktive Container-/Provider-Abnahme bleibt weiterhin offen.

### Optionales imagefestes Pi-Worker-Paket

`docker/compose-next/Dockerfile.pi-worker` ist eine ausdrücklich gewählte
Erweiterung eines genehmigten Ananta-Runtime-Basisimages mit Node/npm.
Das bisherige Standardimage installiert kein Pi-SDK; eine Installation
im beschreibbaren `/app/data/home`-Backend-Prefix erfüllt den neuen
Immutable-Deployment-Nachweis nicht. Der neue Layer installiert daher
SDK 0.85.1 samt gesperrten Transitiven aus
`config/cli_backends/pi/package-lock.json` in
`/opt/ananta/cli-backends/pi/0.85.1`. Das ist exakt das bestehende
CLI-Provisioner-Layout inklusive `node_modules/.bin/pi`.

`npm ci` prüft Integritäten und verwendet getrennte leere User-/Global-
Konfigurationen unter sauberer Umgebung, ohne Lifecycle-Skripte, Audit
oder Funding-Abfragen. Der Build prüft Node ≥22.19, SDK-Name/-Version
und tatsächlichen `createAgentSession`-Import und entfernt Schreibrechte
aus dem Prefix. Laufzeit ist ausdrücklich Worker mit positiver UID/GID;
Hub-URL/Token, Providercredential, Profil, Scratch-Mounts und Readonly-
Rootfs bleiben kontrollierte Deployment-Konfiguration.

Ein Build gegen das lokal gepinnte vorhandene Basisimage und eine reale
netzklose Readonly-Containerprobe bestätigen Node 24.18.0, Pi 0.85.1,
UID1000, Worker-Rolle, SDK-Import und unveränderlichen Katalog-Prefix.
Der erste Build scheiterte an npm-Doppelladen derselben `/dev/null`-
Konfiguration; getrennte leere Dateien beheben dies.

Der Layer übernimmt Anwendungscode und Entry-Point aus dem genehmigten
Basisimage. Die lokale Probe gegen ein vorhandenes Cache-Basisimage
ist daher kein Nachweis der aktuellen gesamten Hub-/Worker-Revision.
Für produktive Zulassung muss der Basisimage-Digest unabhängig mit der
freigegebenen Source-Revision verbunden sein. Es wurden keine laufenden
Deployments geändert und keine Pi-Profil-Aktivierung vorgenommen.

SRP/DIP: Der neue opt-in Layer besitzt nur das feste SDK-Paket und
übernimmt das bestehende Worker-/Hub-Portmodell. Das Standardimage
und andere CLI-Backend-Profile werden nicht umgestellt.

### Aktueller Anwendungscode und echte Runtime-Erkennung im Pi-Image

Die reale CLI-/Runtime-Probe zeigte im gecachten Basisstand
`backend_not_provisionable`: das SDK-Paket war imagefest installiert,
aber der alte Anwendungscode kannte den Pi-Katalogeintrag nicht. Der
Pi-Worker-Layer übernimmt deshalb jetzt die aktuellen `agent/`,
`worker/` und `ananta_contracts/`-Quellen sowie das Pi-Workerprofil.
Er entfernt vorher nur diese alten Image-Verzeichnisse, sodass keine
veralteten Module aus dem Basisimage im neuen Laufzeitbaum verbleiben.

Ein erneuter tatsächlicher Build und ein Readonly-/network-none-/
cap-drop-/no-new-privileges-Container mit UID1000 bestätigen die
aktuelle `CliBackendProvisioner(headless_runner=PiProbeProcess()).status('pi')`:
installed=true, ready, 0.85.1, Probe-RC0, korrekte Version und
unveränderte private Canary-Datei. Der Ergebnisdatei-Isolationspfad
ist bei dieser Probe aktiviert.

Zusätzlich stimmen alle 4.720 Python-/MJS-Quelldateien der drei
Anwendungspakete im Image exakt mit dem aktuellen Worktree überein.
Das ist eine technische Byte-Übereinstimmung dieses lokalen Builds,
keine unveränderliche CI-/Release-Revision oder Hub-reservierte
Release-Evidenz. Separate Hub-/Worker-Containerintegration und
produktive Provider-Abnahme bleiben offen.

SRP: Der optionale Image-Layer besitzt nun SDK und den expliziten
aktuellen Quellbaum; die genehmigte Basis liefert nur Abhängigkeiten
und Entry-Point. Es werden keine Runtime-Rechte oder Queue-/
Worker-Orchestrierungsgrenzen geändert.

### Tatsächlicher Hub-/Worker-App-Start im aktuellen Image

Der neue bounded `pi_worker_startup_gate.py` startet ausschließlich
eigene kurzlebige Container per Image-Digest, ohne Netzwerk und
Capabilities, mit ReadonlyRootfs, nonroot UID und eigener tmpfs-Datenbank.
Die tatsächliche Datenbankinitialisierung und `create_app(testing=True)`
laufen im Container; der Testmodus unterbindet Hintergrundarbeit.
45-Sekunden-Limit und abschließendes Entfernen des eigenen benannten
Containers verhindern interaktives Warten oder verwaiste Fixtures.

Die ersten Startversuche fanden zwei echte Paketierungslücken des
gecachten Basisstands: fehlendes aktuelles Voice-Runtime-Modul und
fehlende GeoMap-Registry. Der Pi-Layer übernimmt jetzt zusätzlich
`voice_runtime/`, GeoMap-Konfigurationen, Schemas und statische Assets.
Es werden ausschließlich Quell-/Template-Verzeichnisse kopiert,
keine Credentials oder Runtime-Daten.

Der Worker-Start besteht mit beiden Native-HTTP-Routen. Ein separater
Hub-Start besteht und besitzt diese Worker-Execution-Routen nicht.
Diese Prüfungen sind echte Container-Startbeobachtungen; sie verbinden
noch nicht beide Apps über RPC mit einem Modellauftrag. Integrierte
getrennte Container-Ausführung und produktive Provider-/Release-Abnahme
bleiben offen.

SRP: Das Image-Layer besitzt die aktuelle Runtime-Paketierung; das
kleine Gate besitzt nur temporäre Startup-Fixtures und Cleanup. Der
Hub bleibt Queue-/Policy-Owner, Worker bleiben Ausführungsinstanzen.

### Getrennte Container: negative RPC-Grenzen

`tests/pi/pi_container_rpc_gate.py` startet zwei aktuelle vollständige
App-Container auf einem eigenen internen Docker-Netz. Beide laufen ohne
Capabilities, mit schreibgeschütztem Root-Dateisystem, nonroot und
getrennter temporärer Datenbank. Es gibt keine veröffentlichten Host-Ports.
Readiness und HTTP-Aufrufe sind begrenzt; ausschließlich die eigenen
Container und das eigene Netz werden abschließend entfernt.

Die tatsächlichen Netzwerkaufrufe bestehen: Hub zu Worker ohne Bearer
liefert 401, mit synthetischem Bearer ohne signierte Zuweisung 422;
Worker zu Hub findet keinen Worker-Ausführungseinstieg (404). Der Bericht
`artifacts/test-gates/pi-container-rpc-boundaries.v1.json` klassifiziert
diese Beobachtungen ausdrücklich als technische negative Tests.
Es werden keine Evidence-Identitäten erfunden oder ausgegeben.

Der erfolgreiche signierte SDK-Auftrag über getrennte Container mit
Hub-Ergebnisannahme sowie produktive Provider-/Release-Abnahme bleiben
offen. Die bestehenden Startup-Gates werden dadurch nicht als vollständige
Legacy-Umstellung umgedeutet. SRP bleibt gewahrt: Das neue Gate besitzt nur
Netzwerk-Fixtures und deren begrenzte Lebensdauer; Produktcode und
Hub-Orchestrierung werden nicht in den Testtreiber verlagert.

### Echter SDK-Durchlauf über getrennte Hub-/Worker-Container

`tests/pi/pi_container_chain_gate.py` führt die beiden vollständigen Apps
in eigenen nonroot Containern mit ReadonlyRootfs und internem Docker-Netz
aus. Der Hub besitzt SQL-Task, Lease, Grant, ContextBundle, Registry und
Signierung. Der Worker erhält über HTTP ausschließlich Provider-Kontext,
öffentliche Verification-Keys und später den signierten Auftrag. Er startet
den tatsächlichen isolierten Pi-SDK-Prozess aus dem unveränderlichen Image.
Es gibt keine gemeinsam beschreibbare Datenbank und keine Host-Port-Freigabe.

Der synthetische Modell-Endpunkt beobachtet vor seinem einzigen Aufruf
den reservierten Hub-RUN mit drei Quellen. Im Prompt erscheinen nur die
freigegebenen Kontextbytes, das Hub-ausgewählte Modell und keine Tools.
Der Worker speichert das Ergebnis; `/result` liefert exakt dasselbe Ergebnis.
Ein zweiter `/execute`-Aufruf wird mit 409 abgewiesen und löst keinen weiteren
Modellaufruf aus. Die tatsächliche Hub-Ergebnisannahme schließt denselben
RUN als `succeeded` und das Versandjournal als `accepted` ab.

Der zusätzliche Modus `--mode context_mutation` verändert den SQL-Kontext
nach der Reservierung. Die Worker-Evidence-Prüfung verweigert den Auftrag
mit 409 vor dem Modellaufruf. Der ursprüngliche RUN bleibt mit seinen drei
ursprünglichen Quellen reserviert; es werden keine neuen Identitäten erzeugt.

Die Prüfung fand eine Runtime-Lücke: Landlock verweigerte dem SDK den
Lesezugriff auf `/etc/hosts` und `/etc/resolv.conf`. Ein Regressionstest
reproduzierte `PermissionError` vor der Korrektur. Das Pi-Launch-Profil
erlaubt nun ausschließlich diese vorhandenen Systemdateien als zusätzliche
Readonly-Ressourcen. Der allgemeine Shell-Isolationsport bleibt unverändert;
privater Resultstore und Worker-Tokens bleiben außerhalb der SDK-Freigabe.
Alle 24 Pi-/Tool-Isolationstests und der relevante Block mit 770 Tests
bestehen. Zwei optionale produktive Live-Smokes bleiben ohne Opt-in übersprungen.

Der Bericht `artifacts/test-gates/pi-container-sdk-chain.v1.json` enthält
die tatsächlichen Beobachtungen und Grenzen. Hub-Gateway-Signatur/Budget-
Speicher, Deployment-Manifest und Modellantwort sind bestehende synthetische
Testports. Nur im eigenen Fixture-Hub wird der Command-Transporthandler
ersetzt; produktive Worker-Registrierungs-Authentifizierung wird damit nicht
als abgenommen behauptet. Das Worker-Adapter-Setup ist explizite Testkomposition.
Die Prüfung aktiviert kein produktives Pi-Profil und erfüllt keine Release-Gates.

Wiederholung nach eigenem Image-Build:

```bash
python -m tests.pi.pi_container_chain_gate --image sha256:<vollstaendiger-image-digest>
python -m tests.pi.pi_container_chain_gate --image sha256:<vollstaendiger-image-digest> --mode context_mutation
```

SRP/DIP: Das Worker-Launch-Modul besitzt die SDK-Ressourcen und verwendet
den vorhandenen Isolationsport. Der Fixture-Treiber besitzt ausschließlich
seine Container und deren begrenzte Lebensdauer. Bestehende globale
Konfigurationshelfer und der zentrale Gateway-Dispatcher bleiben bekannte
Strukturschulden; neue Worker-Orchestrierungsrechte entstehen nicht.

Die vollständige PI-T05-Abnahme bleibt offen: Resume/Sitzungszweige und
Pi-Artefaktreferenzen sind derzeit ausdrücklich deaktiviert. Der erfolgreiche
Read-only-Turn und gespeicherte Ergebnisabruf ersetzen diese Anforderungen
nicht. Produktive Deployment-/Provider-/Release-Abnahme ist zusätzlich
von den synthetischen Container-Prüfungen zu unterscheiden.

### Pi-Antwort als Hub-registriertes Artefakt

Die bisher pauschal ausgeschlossene Pi-Artefaktübergabe unterstützt jetzt
ein ausdrücklich deklariertes Antwortartefakt. Der Hub benennt den Output
im signierten Artefakt-Scope des Auftrags und ergänzt am Node:

```yaml
output_artifacts: [answer]
metadata:
  pi_result_artifact:
    output_name: answer
    source: assistant_answer
    filename: answer.md
    media_type: text/markdown
```

Zulässig sind `text/plain` und `text/markdown`, ein einzelner Dateiname
ohne Pfad und exakt ein deklariertes Output. `answer` ist ein Beispielname;
er muss mit `output_name` und dem Hub-signierten `allowed_artifacts`-Scope
übereinstimmen. Andere Quellen, zusätzliche Felder, Traversal-Pfade,
unbekannte Medien und unautorisierte Output-Namen werden verweigert.

Im bestehenden Hub-Inventar muss `policy.preauthorized_result_artifacts`
zusätzlich ausdrücklich `true` sein. Der neue boolesche Pin ist standardmäßig
`false`; Read-only-Freigabe allein erlaubt keine Artefaktveröffentlichung.
Bestehende No-Artifact-Policy-Digests bleiben unverändert. Stringwerte wie
`"true"` werden abgewiesen. Kein Deployment oder Profil wird automatisch aktiviert.

Der Worker bereitet den vorhandenen `NativeArtifactPublisher` vor, bevor
er Credentials, Modellbudget oder SDK beansprucht. Nach dem erfolgreichen
Turn überträgt er nur die Antwortbytes; das SDK erhält weder Schreibtools
noch Zugriff auf Worker-Resultstore oder Hub-Tokens. Der vorhandene Hub-Ingress
prüft aktuelle Task-/Assignment-/Lease-Fakten, speichert unveränderliche Bytes
und registriert die Hub-Artefaktidentität. Der Worker erfindet keine Identität.

Die Pi-Ergebnisannahme prüft im selben Task-Schreibvorgang die tatsächliche
SQL-Registrierung einschließlich Command-/Assignment-/Lease-Provenienz.
Dateiname, Medienart, Größe und Hash müssen der deklarierten Antwort
entsprechen. Die technische Hub-Receipt bindet die geprüfte Artefaktversion.
Exakte Receipt-Wiederholung und RUN-Abschluss prüfen diese Registrierung
erneut; fehlende oder veränderte Artefakte werden nicht durch Replay geheilt.
Die Node-Deadline wird vor und nach Veröffentlichung geprüft.

Der produktive Task-Forwarding-Pfad bewahrt die kanonischen Pi-Referenzen
und führt sie nicht nochmals durch die allgemeine Artefaktregistrierung.
`execution_artifacts`, verschachteltes Worker-Ergebnis und Native-Verifikation
müssen übereinstimmen. Die bestehende breite Forwarding-Fassade bleibt
bekannte SRP-Strukturschuld; Pi-Projektion, Declaration und transaktionale
Artefaktprüfung liegen in eigenen Modulen und verwenden kleine Ports.

Die gezielten Tests benutzen tatsächlichen Hub-Dateispeicher und SQL:
Idempotente Übertragung erzeugt keine zweite Version; geänderte Antwortbytes,
beschädigte oder fehlende Registrierungen und abgelaufene Leases verweigern
den Abschluss. Der echte gepinnte SDK-Prozess besteht Plain-/Artefakt- und
Kontextmutationsfälle. `--mode answer_artifact` im Container-Gate besteht
mit einem beim Hub gespeicherten Antwortartefakt, einem Modellaufruf und
Abschluss desselben vorab reservierten RUN. Der relevante Block besteht
mit 809 Tests, zwei nicht aktivierten Live-Smokes und vier bestehenden
Startup-Deprecation-Warnungen. Details und Fixture-Grenzen stehen in
`artifacts/test-gates/pi-answer-artifact-admission.v1.json`.

Zum Abschalten weiterer Artefaktaufträge den zusätzlichen Policy-Pin auf
`false` setzen. Bereits laufende Aufträge bei Bedarf über die vorhandenen
Hub-Grant-/Lease-Widerrufe stoppen; weder Identitäten noch abgeschlossene
Receipts umschreiben oder durch Wiederholung neu ausführen.

Resume/Sitzungszweige, Workspace-Dateiextraktion und andere Artefaktquellen
bleiben offen. Produktive Worker-Registrierungs-Authentifizierung und
Provider-/Release-Abnahme werden durch synthetische Fixture-Handler nicht
behauptet. PI-T05 bleibt deshalb offen.

### Native-Planprüfung und Pi-Capability (2026-10-12)

Die Hub-Planprüfung kennt jetzt `coding.agent.pi` als delegierte Capability.
Zuvor wies sie vollständige Native-Pläne mit dieser Capability bereits vor
der Delegation zurück, obwohl der opt-in Pi-Worker-Handler vorhanden war.
Task-Kind und Capability stehen gemeinsam in `ananta_contracts.pi_native`;
der Hub importiert dafür keinen Worker-Ausführungscode. Die bisherigen
Konstantenimporte aus `pi_node` bleiben als Re-Exports kompatibel.

Die Erkennung erteilt keine Freigabe: Hub-Policy, Worker-Capability,
Worker-Policy, opt-in Profil, signierte Delegation und Evidence-Prüfungen
bleiben erforderlich. Regressionen prüfen einen tatsächlich vom Hub
signierten delegierten Knoten, Worker ohne Capability, Worker-Policy-Ablehnung,
Hub-Policy-Ablehnung vor Task-Erzeugung und eine weiterhin abgewiesene unbekannte
Capability. Queue, Policy und Ausführungshandler dieser fünf Fälle sind
synthetisch; eine produktive Modell- oder Release-Abnahme wird daraus nicht
abgeleitet. SRP/DIP: gemeinsames Vertragsvokabular statt Hub-Kopplung an den
Worker-Handler; bestehende Pi-Ausführungsports bleiben unverändert.

Der abschließende Pi-/Native-/Taskadapter-/Maintenance-Regressionsblock besteht
mit 642 Tests in 45.50 Sekunden; nur zwei nicht aktivierte externe Live-Smokes
sind übersprungen. Die vorhandenen gepinnten SDK-Isolations- und SQL-Evidence-
Kettentests laufen mit dem explizit konfigurierten lokalen Node/SDK mit.
Bericht: `artifacts/test-gates/pi-native-capability-delegation.v1.json`.

Der Worker-Prozessadapter importiert `agent.cli_backends.pi_runtime` jetzt
explizit. Der Architekturdetektor kennt zusätzlich genau diesen SDK-Resolver,
`coding_agent_process` (begrenzte Prozessausführung) und `provisioning`
(Worker-lokaler Runtime-Status/Probe). Diese geprüften Ausführungsbausteine liegen
im durch AGENTS.md vorgegebenen CLI-Namespace und importieren keine Hub-Task-
oder Delegationsdienste. Eine pauschale Freigabe von `agent.cli_backends` wird
nicht eingeführt. Die Namespace-/Packaging-Schuld bleibt sichtbar.
