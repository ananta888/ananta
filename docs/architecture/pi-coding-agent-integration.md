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
`tests/test_pi_http_transport.py` im regulären Pytest-Gate erfasst.

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
zusätzliche Attempt-/Retry-Protokolle werden nicht stillschweigend übergangen,
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
