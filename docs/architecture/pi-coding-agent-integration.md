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
