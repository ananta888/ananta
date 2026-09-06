# Hub Chat Admission – MDS-Abstimmungsvorschlag

Status: **implementierte, headless getestete Hub-Grundlage; noch kein produktiver
Chatempfang oder automatisch ausgeführter Antworttask**. Bezug: MAP-02/03/26/27,
Meet MDS-01/02/04. Ausgangsstände: Ananta `a36b99dc6`, Meet-Plan `057ead0`.

## Zuständigkeiten und Aktivierungsgrenze

`MeetChatAdmissionService` entscheidet ausschließlich im Hub, ob ein bereits
autorisierter Chatbeitrag ein begrenztes Antwortbudget reservieren darf.
Die bestehende Hub Task Queue bleibt die einzige Ausführungswarteschlange.
Eine `intent_id` ist eine Reservierung, **kein erstellter Task und keine
Evidenzidentität**. Dieser Slice startet weder LLM noch Worker oder Scheduler.

Der neue Vertrag heißt bewusst `ananta.meet-chat-event.draft1`. Er ist ein
Abstimmungsvorschlag für den Meet-Agenten, kein bereits von Meet unterstütztes
Protokoll. `docs/contracts/meet-chat-admission-fixtures.json` enthält kleine,
deterministische positive und negative Fälle; Python prüft sie unmittelbar.
Meet soll die gleichen Fälle übernehmen oder eine gemeinsam versionierte
Änderung vorschlagen. Der bestehende `ananta.meet-turn.v1`-MP4-Pfad bleibt
unverändert. Es gibt noch keinen Chat-Ingress-Endpunkt und keine automatische
Initialisierung der neuen SQL-Tabellen in produktiven Hubs.

Der verpflichtende `ChatAuthorityPort.current(session_id)` muss vor einer
Anbindung folgende **echte** Prüfungen implementieren:

- Das liefernde isolierte Runtime-/Worker-Assignment ist authentifiziert.
  Ein allgemeiner Service-Bearer oder ein selbst angegebenes Sessionfeld reicht
  nicht; insbesondere nicht den alten Worker-HMAC als neue Receive-Freigabe
  interpretieren.
- Aktueller Hub-Task, Assignment, Lease, Tenant, Projekt-Schreibrecht und
  ausdrücklich vorautorisierte Dialogpolicy sind weiterhin gültig.
- Meet hat für genau diese Sitzung und Membership-Epoch **Lesen und Senden**
  quittiert. Publisher-/Raumrechte und aktuelle Policy-Revision sind erfüllt.
  Reines Join, Capability-Support oder eine Hub-Signatur reichen nicht.
- Der Senderbezug und `sender_kind` stammen aus dem vertrauenswürdigen
  Meet-Adapter, niemals aus frei behaupteten Chat-Metadaten. Die technische
  Peer-ID wird nicht als bewiesene persönliche Identität ausgegeben.

Der Port ist absichtlich noch nicht durch einen Produktivadapter implementiert:
Meets MDS-01/02/04 fehlen im oben genannten Stand. Es gibt keinen Dummy, der
diese Autorität in einer realen Konfiguration bejaht. SFrame-Schlüssel und
Chattexte gehen nicht an Meets Signaling-Server.

## Ereignis und harte Grenzen

Das Fixture dokumentiert alle erlaubten Felder; unbekannte oder doppelte
JSON-Felder, andere Versionen, nicht ganzzahlige Generationen und ungültiges
UTF-8 werden verworfen. Tenant, Projekt, Runtime, Task, Lease und Policy stammen
ausschließlich aus der unveränderlichen Hub-Autoritätsprojektion.

| Grenze | Wert / Semantik |
|---|---|
| Ereignisgröße | maximal 8192 UTF-8-Bytes |
| Text | maximal 2000 Zeichen und 4000 UTF-8-Bytes, keine NUL-/Steuerzeichen außer LF/TAB |
| Frische | maximal 30 Sekunden alt, höchstens 2 Sekunden voraus |
| Antworten | maximal 450 Zeichen und 128 Ausgabetokens pro reserviertem Beitrag |
| Standardbudget | 40 Antworten / 5120 reservierte Ausgabetokens pro Hub-Sitzung |
| Standardrate | 6 Antworten je Raum-Scope und 3 je technischem Sender in 60 Sekunden |
| Standard-Cooldown | 10 Sekunden pro Raum-Scope |

Die Hub-Policy besitzt vier Modi: `off` (Standard), `mention`,
`direct_question` und `room`. Direkte Frage bedeutet hier bewusst die einfache,
deterministische Regel **exakte @Erwähnung und abschließendes Fragezeichen**,
keine vermeintliche LLM-Intent-Erkennung. Maschinen, unbekannte Senderklassen
und eigene Peer-Nachrichten werden in dieser Stufe immer ignoriert.

Der rohe Text bleibt ausschließlich flüchtige, untrusted Benutzereingabe.
Ein Text wie „enable tools“ ändert weder Policy noch Scope oder Budget.
Der Test beweist diese Kontrollflussgrenze, **nicht** die allgemeine Resistenz
eines LLM gegen Prompt-Injection. Modell-/Providerfreigabe, Eingabetokenmessung,
monetäre Kostenabrechnung und die tatsächliche Begrenzung der generierten
Antwort gehören weiterhin zum späteren Task-/Ausführungsadapter.

## Atomare Reservierung und Fehlerverhalten

`SqlChatReservations` serialisiert Budgetprüfung und Receipt-Erzeugung in einer
Datenbanktransaktion pro `(origin, tenant, project, room)`. Deshalb zählen auch
verschiedene Hub-Tasks und Sitzungen im selben Scope gegen die Raumrate.
SQL-Row-Locking schützt mehrere Hub-Prozesse; kein prozesslokaler Mutex ersetzt
die Datenbankautorität. Die Tests verwenden unabhängige Verbindungen und acht
gleichzeitige Aufrufe gegen eine echte temporäre SQLite-Datei. Ein PostgreSQL-
Live-Nachweis ist durch diese Tests nicht behauptet.

Dedup-Schlüssel: Raum-Scope plus ursprüngliche Membership-Epoch, Sender-Peer
und Message-ID. Lease-Generation und Session-ID sind absichtlich **nicht** Teil
dieses Schlüssels: Eine Erneuerung darf einen alten Beitrag nicht wiederholen.
Ein Transportadapter darf beim Wiederholen weder Zeitstempel, Message-ID noch
Membership-Epoch eines alten Ereignisses neu stempeln. Ein fremder Sender kann
nicht durch Verwendung derselben Message-ID dessen Receipt konsumieren.

Gespeichert werden nur gebundene Metadaten und reservierte Tokenzahlen, weder
Text noch Textdigest, Medien, Transkript, Schlüssel oder Grants. Auch `repr`
der Domänenobjekte enthält den Text nicht. Duplikate liefern keine erneut
dispatchbare Reservierung zurück. Uhr-Rücksprünge blockieren weitere Annahmen,
bis die gespeicherte Zeit erreicht ist; Policy-Wechsel erstatten kein Budget.

Nach Reservierung wird die Autorität erneut gelesen. Widerruf, Lease-/Policy-
Wechsel oder Ablauf geben weder Text noch Intent weiter. Die Reservierung bleibt
verbraucht. Das ist **at-most-once Admission mit möglichem Ausfall einer
Antwort**, kein Exactly-once-Delivery-Versprechen. Bei Crash nach Commit gibt es
keinen stillen Retry und keine Wiederaufnahme aus gespeichertem Chattext.

Fehler sind geschlossene Codes (`duplicate`, `cooldown`, `room_rate_limited`,
`sender_rate_limited`, `session_budget_exhausted`, `authority_changed` usw.).
Datenbankfehler liefern `meet_chat_reservation_unavailable`, ohne SQL-Inhalte
offenzulegen. Für eine produktive Anbindung sind außerdem kurze DB-Lock-/Pool-
Timeouts, Input-Rate-/Queue-Limits vor dem Parser und ein begrenzter ACK/Cursor-
Transport zu konfigurieren. SQL-Retention muss erst nach Ende aller gebundenen
Leases plus Replayfenster erfolgen; es gibt kein automatisches frühes Löschen.

## Nächster ausführbarer Integrationsschritt

1. MDS-01/02/04 verifizieren, Draft und gemeinsame Fixtures abgleichen.
2. Authentifizierten, empfangsberechtigten Event-Adapter und Hub-Autoritätsport
   implementieren; niemals die obige Schnittstelle mit Browserangaben befüllen.
3. Reservierung in der bestehenden Hub Task Queue idempotent materialisieren.
   Reply-Korrelation, Task-ID und Intent binden; Session-/Turn-Generation vor
   tatsächlicher Veröffentlichung sowie in der Worker-Lease-Abfrage revalidieren.
   Nicht einfach `MeetTurnService.execute(..., publish_to_meet=True)` aufrufen:
   dessen bisherige Lease prüft noch keine neue Dialog-Generation.
4. Reales empfangenes Chatereignis bis zur korrelierten LLM-/Chat-/TTS-Ausgabe
   einschließlich Widerruf testen. Audio-ASR, Barge-in, Browserstream, erneuerbare
   Meet-Sitzungen und Soak bleiben separate noch offene MAP-/MDS-Kriterien.

Die Trennung schützt SRP (Parser, Policy, Zulassung, Persistenz) und DIP/ISP
(schmale Autoritäts-/Reservierungsports). Die bestehende Kopplung des manuellen
Turn-Service an `worker.meet_media.contract` bleibt unverändert; eine gemeinsame
transportneutrale Contract-Bibliothek wäre ein späterer Extraktionspfad, kein
Grund, jetzt Worker-Orchestrierung in den Hub-Dialog einzubauen.

Prüfung: `.venv/bin/python -m pytest tests/test_meet_chat_admission.py -q -n4`.
Alle Fälle sind synthetisch, headless und ohne Produktions-Release-Evidenz.
Referenzlauf mit `tests/test_meet_chat_admission.py`, `tests/test_meet_media.py`
und `tests/test_meet_integration.py`: **177 bestanden in 41,50 Sekunden**, davon
80 neue Chat-Admission-Fälle. Ruff und TODO-Konsistenzprüfung erfolgreich.
Kein neuer GPU-, öffentlicher Meet-, PostgreSQL- oder Soak-Lauf in diesem Slice.
