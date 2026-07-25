# JMAP Provider

Dieses Runbook beschreibt die JMAP-Providergrenze von Ananta, die
repository-eigene Testinfrastruktur und die Voraussetzungen für belastbare
Provider-Evidenz. Es ersetzt weder die
[provider-neutrale TUI-Dokumentation](operator-tui-mail-client.md) noch das
[Migrationsrunbook](migrations/imap-to-mail-account-v2.md).

## Architekturgrenze

JMAP erweitert die bestehende Hub-Worker-Architektur additiv:

- `MailProviderRouter` bindet einen aufgelösten `MailAccountV2` an einen
  Provider.
- Kleine Provider-Ports trennen Discovery, Lesen, Body/Blob, Mutation und
  Sync.
- Der Hub besitzt Taskqueue, Accountzustand, Rolloutentscheidung, Lease,
  Cutover und Audit.
- Ein Worker darf genau die delegierte Operation ausführen und ein begrenztes
  Ergebnis zurückgeben. Er darf keine anderen Worker oder Mail-Tasks
  orchestrieren.
- IMAP bleibt als separater Adapter erhalten; JMAP ersetzt ihn nicht durch
  einen Big-Bang-Refactor.

Diese Trennung schützt SRP, OCP und DIP: Protokollimplementierungen hängen an
fokussierten Ports, während Policy und Orchestrierung im Hub bleiben.

## Discovery und Session

JMAP wird über eine explizite Session-URL oder über
`https://<domain>/.well-known/jmap` entdeckt. Eine gültige Session muss
mindestens enthalten:

- `urn:ietf:params:jmap:core`
- `urn:ietf:params:jmap:mail`
- einen Mail-Account in `accounts` und `primaryAccounts`
- `apiUrl`, `downloadUrl`, `uploadUrl` und `state`
- positive, lokal begrenzte Core-Limits

Ananta übernimmt Serverlimits niemals ungeprüft. Requestgröße, Responsegröße,
Calls pro Request, Objekte pro `get`/`set`, Redirects, Retries, Change-Seiten
und Rebuild-Größe bleiben zusätzlich lokal begrenzt.

## Endpoint- und Credential-Policy

- Öffentliche Endpunkte benötigen HTTPS.
- Externer Netzwerkzugriff ist standardmäßig deaktiviert.
- Lokales HTTP ist nur mit aktivierter Local-Endpoint-Policy, exaktem Host und
  passenden CIDR-Grenzen erlaubt.
- DNS-Ergebnisse werden geprüft; private, Loopback-, Link-local- oder andere
  nicht öffentliche Adressen sind ohne diese lokale Ausnahme gesperrt.
- Session- und API-URLs dürfen keine Userinfo, Fragmente oder unerwartete
  Query-Parameter enthalten.
- Verwandte URLs müssen denselben Ursprung verwenden oder explizit
  allowlisted sein.
- Download-, Upload- und Event-Source-Templates müssen exakt die erwarteten
  Variablen enthalten. Werte werden vor Expansion URL-kodiert.
- Credentials stammen ausschließlich aus `credential_ref`. Sie gehören nicht
  in `provider_config`, Tasks, Resultate, Logs oder Fixtures.

## Sync, Bodies und Mutationen

Der Provider hält JMAP-Zustände separat von der TUI-Darstellung:

- Mailbox- und Email-Metadaten werden inkrementell über JMAP-Zustände
  synchronisiert.
- Ein begrenzter Rebuild ist nur nach der vorgesehenen Rebuild-Policy erlaubt.
- Body- und Blob-Abrufe erfolgen erst nach einer expliziten TUI-Aktion.
- Mutationen laufen als eigene Hub-Tasks und liefern pro Objekt ein begrenztes
  Ergebnis.
- Push ist standardmäßig deaktiviert; Polling bleibt policy-gesteuert.
- Circuit Breaker sind account- und providerbezogen. Metriken erhalten keine
  Account-ID als Label.

Ein `mail_message_ref.v2` trägt den Provider und einen
protokollspezifischen Locator. JMAP-`Email/id`, Blob-ID und IMAP-UID sind nicht
austauschbar.

## Repository-eigener JMAP-Contract

Die lokale Abnahme verwendet ausschließlich den in
`tests/e2e/mail_ananta_composition_harness.py` definierten
`ContractJmapAdapter`. Er implementiert die für diesen Track benötigten
standardisierten JMAP-Core- und JMAP-Mail-Antworten deterministisch hinter der
realen Transportabstraktion.

Der Contract:

- lädt kein Image und keinen fremden Servercode,
- startet keinen Container und öffnet keine externe Netzwerkverbindung,
- verwendet keine vendorspezifische Management-Capability,
- isoliert State pro Testinstanz,
- prüft die reale Hub-Submission, Lease/Fencing, Worker-Composition,
  Discovery, Sync, Body-Grant und Mutation.

Die Dependency-Injection-Grenze schützt DIP und LSP: Der produktive
`JmapHttpTransport` bleibt gegen seinen schmalen Adapter-Port programmiert,
während der Test eine deterministische Implementierung einsetzt.

## Deterministische Mock-Fixtures

| Datei | Zweck |
|---|---|
| `tests/fixtures/jmap/session.valid.json` | Core-/Mail-Capabilities, URLs, Account und feste Limits |
| `tests/fixtures/jmap/mailbox-get.valid.json` | einzelne Inbox ohne volatile IDs oder Zeitstempel |
| `tests/fixtures/jmap/email-query-get.valid.json` | Query plus metadata-only `Email/get`, ausdrücklich ohne `bodyValues` |

Die Hostnamen verwenden die reservierte `.test`-Zone. IDs, States und
Zeitwerte sind konstant. Die Fixtures sind keine Exporte einer Live-Mailbox und
enthalten keine Credentials, echten Nachrichten oder erzeugten Lauf-IDs.

## Externe Evidenzgrenze

Der lokale Contract belegt das Verhalten von Ananta, nicht die
Interoperabilität mit einer konkreten externen Implementierung. Vor einem
Production-Default bleibt deshalb ein vendorneutraler Provider-Smoke nötig:

- Operator stellt eine kompatible JMAP-Session und kurzlebige Credentials
  bereit.
- Ananta führt Discovery, Authentifizierung, Capability-Prüfung,
  Metadatensync, explizites Body-Laden und eine kontrollierte Mutation aus.
- Das Ergebnis enthält ausschließlich redigierte Referenzen und tatsächlich
  bereitgestellte `RUN_*`-IDs.

Dieser externe Smoke ist kein lokaler Testbestandteil und darf nicht
stillschweigend einen Server herunterladen oder starten.

Der ausführbare Gate-Pfad ist `tests/e2e/test_jmap_live_provider.py`. Er
verwendet ohne Transport-Mock die produktive Endpoint-Policy und den echten
HTTP-Adapter. Er ist nur mit vollständiger Opt-in-Konfiguration, einem
dedizierten Testkonto, einer tatsächlich bereitgestellten `RUN_*`-ID und der
Bestätigung einer reversiblen Keyword-Mutation aktiv. Die Variablen und das
redigierte Evidenzformat sind in
[jmap-test-stack.md](jmap-test-stack.md) dokumentiert.
