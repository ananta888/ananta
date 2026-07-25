# JMAP Provider

Dieses Runbook beschreibt die JMAP-Providergrenze von Ananta, die isolierte
Stalwart-Testinfrastruktur und die Voraussetzungen für belastbare
Live-Evidenz. Es ersetzt weder die
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

## Isoliertes Stalwart-Profil

Das Testprofil verwendet exakt:

```text
stalwartlabs/stalwart:v0.16.10-alpine@sha256:9dc88c60cb9aac1f7a42dd8fefe868567d68c836017bad5521caea6d05debd32
```

Start aus dem Repository-Root:

```bash
docker compose \
  -p ananta-jmap-test \
  -f docker/compose-next/compose.jmap-test.yml \
  --profile jmap-test \
  up -d
```

Das Profil veröffentlicht nur den Management-/HTTP-Port auf Loopback:

```text
http://127.0.0.1:18080
```

Abweichende Werte benötigen gemeinsam
`ANANTA_JMAP_TEST_HTTP_PORT` und `ANANTA_JMAP_TEST_PUBLIC_URL`, damit das
Discovery-Dokument keine falschen URLs publiziert. Der Healthcheck ruft
`http://127.0.0.1:8080/healthz/live` im Container auf. Dieser Endpunkt belegt
nur Liveness, nicht Datastore-Readiness, JMAP-Capabilities, Authentifizierung
oder Seed-Daten.

Die beiden benannten Volumes sind nur dem isolierten Compose-Projekt
zugeordnet. Vollständiges Entfernen der lokalen Testinstanz:

```bash
docker compose \
  -p ananta-jmap-test \
  -f docker/compose-next/compose.jmap-test.yml \
  --profile jmap-test \
  down -v
```

Kein produktiver Stack darf diese Volumes, den Loopback-HTTP-Listener oder
Test-Credentials wiederverwenden.

## Deterministische Mock-Fixtures

| Datei | Zweck |
|---|---|
| `tests/fixtures/jmap/session.valid.json` | Core-/Mail-Capabilities, URLs, Account und feste Limits |
| `tests/fixtures/jmap/mailbox-get.valid.json` | einzelne Inbox ohne volatile IDs oder Zeitstempel |
| `tests/fixtures/jmap/email-query-get.valid.json` | Query plus metadata-only `Email/get`, ausdrücklich ohne `bodyValues` |

Die Hostnamen verwenden die reservierte `.test`-Zone. IDs, States und
Zeitwerte sind konstant. Die Fixtures sind keine Exporte einer Live-Mailbox und
enthalten keine Credentials, echten Nachrichten oder erzeugten Lauf-IDs.

## Kein behauptetes Live-Seed-E2E

Das Compose-Profil seedet absichtlich nichts. Für einen echten Test fehlen
derzeit:

- ein nicht interaktiver, versionsgebundener Stalwart-Bootstrap,
- ein Test-Domain- und Account-Objekt,
- ein SecretStore-Eintrag oder kurzlebiges Test-Credential,
- eine reproduzierbare Mail-Injektion,
- eine lokale Endpoint-Allowlist für Anantas JMAP-Policy,
- ein Harness für Discovery, Auth, Mailbox-Sync, explizites Body-Laden und
  Teardown,
- eine Evidence-Ausgabe, die nur tatsächlich beobachtete Resultate erfasst.

Bis diese Punkte implementiert und ausgeführt sind, sind Formulierungen wie
"live seeded", "E2E bestanden" oder "Provider verifiziert" unzulässig.

## Lizenz und Freigabeblocker

Die Integrationsvorgabe verlangt für Stalwart die Wahl zwischen
**AGPL-3.0** und **Stalwart Enterprise License v1 (SELv1)**. Für jeden
Deployment-Kontext muss die tatsächlich gewählte Lizenz samt
Artifact-Digest, Lizenztext und Freigabe dokumentiert werden. Das
Ananta-Repository erteilt selbst keine Stalwart-Lizenz.

Es besteht eine reale Upstream-Abweichung: Der aktuell abrufbare Quell-Tag
[`v0.16.10`](https://github.com/stalwartlabs/stalwart/tree/v0.16.10#license)
und dessen
[`LicenseRef-SEL.txt`](https://github.com/stalwartlabs/stalwart/blob/v0.16.10/LICENSES/LicenseRef-SEL.txt)
bezeichnen die Alternativlizenz als **Stalwart Enterprise License v2
(SELv2)**, nicht SELv1. Der
[AGPL-3.0-Text](https://github.com/stalwartlabs/stalwart/blob/v0.16.10/LICENSES/AGPL-3.0-only.txt)
ist im Tag vorhanden.

Daraus folgen zwei zulässige Freigabepfade:

- AGPL-3.0 wird nach Prüfung der konkreten Nutzungs-, Änderungs- und
  Bereitstellungspflichten freigegeben.
- Für SELv1 wird ein gültiger, zum gepinnten Artifact passender Lizenztext und
  Berechtigungsnachweis durch Legal/Procurement bereitgestellt.

Der Upstream-Tag allein ist kein SELv1-Nachweis. Ohne geklärte Lizenzwahl darf
das Image nicht als freigegebene Produktionsabhängigkeit oder distributable
Testabhängigkeit behandelt werden. Diese Dokumentation ist keine
Rechtsberatung.

## Quellen

- [Stalwart JMAP overview](https://stalw.art/docs/http/jmap/)
- [Stalwart Docker installation](https://stalw.art/docs/install/platform/docker/)
- [Stalwart liveness and readiness endpoints](https://stalw.art/docs/cluster/orchestration/kubernetes/#liveness-and-readiness-endpoints)
