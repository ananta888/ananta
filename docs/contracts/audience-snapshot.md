# Immutable Audience Snapshot v1

## Zweck und Autoritaet

`ananta.webrtc.audience-snapshot.v1` friert die vom Hub individuell autorisierte Empfaengermenge fuer genau einen Tenant, Raum und eine Publication ein. Der Snapshot ist eine Projektion kanonischer Hub-Berechtigungen, keine neue Berechtigungsquelle.

Der Hub bleibt alleiniger Eigentuemer von Membership, Consent, Grants, Policy, Key-Epoch, Projektion und Lebenszyklus. Worker und SFU duerfen den Snapshot weder erzeugen noch erweitern noch eine fehlende Referenz erraten. Nicht aufloesbare, unbekannte oder veraltete Referenzen werden fail-closed abgelehnt.

Das normative Schema ist `schemas/webrtc/audience_snapshot.v1.json`. JSON-Schema prueft Form und absolute Obergrenzen. Die in diesem Dokument beschriebenen zustandsabhaengigen Regeln sind ebenfalls normativ; reine Schema-Validierung reicht fuer Conformance nicht aus.

## Verantwortungsgrenzen

| Bereich | Hub-persistiert | SFU-sichtbar |
|---|---:|---:|
| Tenant-, Room-, Publication- und Policybindung | ja | nein, ausserhalb dieses Audience-Payloads durch den Route-Vertrag gebunden |
| Member-, Membership-, Consent- und Grantreferenzen | ja | nie |
| Membership-, Consent- und Media-Key-Epoch | ja | nein, ausserhalb dieses Audience-Payloads durch den Route-Vertrag gebunden |
| Digest-Key-Referenz und -Version | ja | nie |
| Snapshot-spezifische Receiver-Handles | ja | ja |
| Audience-Digestwert | ja | ja |

Nur das geschlossene Objekt `sfu_projection` darf als Audience-Payload an eine SFU-Control-Grenze kopiert werden. Es enthaelt exakt `receiver_handles` und `audience_digest`. Consent-, Rollen-, Membership-, Grant-, Policy- oder Key-Listen sind an dieser Grenze verboten. Ein Worker erhaelt keine eigenstaendige Autoritaet zum Aufloesen oder Erweitern der Handles.

Diese Trennung schuetzt SRP und DIP: Autorisierungslogik bleibt im Hub-Domain-Service; Persistenz, Digest-Key-Provider und SFU-Adapter implementieren schmale Ports. Der SFU-Adapter konsumiert eine minimale opaque Projektion statt Hub-Persistenzmodelle zu kennen.

## Snapshot- und Versionsinvarianten

Ein gespeicherter Snapshot und seine Member-/Grant-Kindzeilen sind append-only. `immutable` ist immer `true`. Status, Tombstone und Purge gehoeren in separate Lifecycle-Metadaten und duerfen den signifikanten Snapshot-Inhalt nicht umschreiben.

`projection_version = 1` besitzt `change_kind = initial` und keine Vorgaengerreferenz. Jede der folgenden Aenderungen erzeugt atomar einen neuen unvorhersagbaren `snapshot_id` und eine strikt groessere `projection_version`:

- Join oder Leave
- Revoke
- Grant-Inhalt oder Grant-Version
- Consent oder Consent-Epoch
- Membership oder Membership-Epoch
- Media-Key-Epoch
- gebundene Policy oder Policyversion

Ein Nachfolger verweist auf einen existierenden Vorgaenger desselben Tenant-/Room-/Publication-Scopes. Seine ID muss verschieden und seine Version groesser sein. Die Publish-Transaktion verwendet Hub-seitiges CAS auf der aktuellen Projektionsversion. Ein CAS-Konflikt wird nicht durch Ueberschreiben geloest, sondern durch erneutes Lesen, erneute Autorisierung und einen weiteren neuen Snapshot.

Alte Snapshots bleiben unveraendert, sind nach Ablauf oder Ersetzung aber nicht mehr fuer neue Route-Intents verwendbar. Reconciliation darf nie aus einem alten Snapshot neue Berechtigung ableiten.

## Kanonische Audience

`hub_audience.members` ist strikt aufsteigend nach `member_ref` sortiert. Jeder `member_ref`, `membership_ref`, `consent_ref` und `receiver_handle` kommt hoechstens einmal vor. Pro Mitglied ist `grant_refs` strikt aufsteigend nach `grant_ref` sortiert; ein `grant_ref` darf snapshotweit nur einmal vorkommen.

Alle Vergleiche fuer diese Ordnung verwenden die unsigned ASCII-Bytes der durch das Schema auf ASCII beschraenkten IDs. Gleichheit oder eine absteigende Reihenfolge ist ungueltig. `uniqueItems` im Schema ist nur eine erste Schranke; der semantische Validator prueft zusaetzlich ID-basierte Duplikate.

Jeder Grant wird bei Erstellung transaktional aus kanonischem Hub-Zustand aufgeloest. Folgende Werte muessen exakt passen:

- Tenant, Room und Publication
- Mitglied und Membershipreferenz
- Consentreferenz und Consent-Epoch
- Membership-Epoch und Media-Key-Epoch
- Grant-Version
- aktive Gueltigkeit fuer das gesamte Intervall von `created_at` bis einschliesslich `expires_at`

Ein zwischenzeitlich revozierter oder ersetzter Grant macht den alten Snapshot nicht mutierbar. Er verhindert dessen weitere Verwendung und loest einen neuen Snapshot mit neuer ID, groesserer Version und Rekey-/Route-Reconciliation aus.

## Receiver-Handles und Nicht-Verknuepfbarkeit

Ein `receiver_handle` repraesentiert mindestens 128 Bit unvorhersagbare Entropie in ungepaddetem Base64url und ist nur fuer einen `snapshot_id` gueltig. Der Hub erzeugt Handles mit einem kryptographisch sicheren Zufallszahlengenerator oder einer separaten Hub-only PRF. Rohidentitaeten, Member-IDs oder zaehlbare Sequenzen duerfen nicht kodiert werden.

Handles werden bei jedem Nachfolger rotiert. Sie duerfen insbesondere nicht ueber Room-, Publication- oder Key-Epoch-Grenzen wiederverwendet werden. Der Hub fuehrt fuer die kurze Retentionfrist einen Kollisions-/Reuse-Index. Ein beobachtetes Reuse wird vor der Digestpruefung mit einem grenzspezifischen Reason-Code abgelehnt.

Der Digest ist ebenfalls scope-abhaengig. Selbst dieselbe Empfaengermenge erzeugt wegen Scope, Epochen, Snapshot-ID, Version und Gueltigkeitsfenster einen anderen Wert. Damit entsteht weder ein stabiler Audience-Identifier noch ein offline enumerierbarer Rollen- oder Mitgliederhash.

## Normative Digestbildung

`member_digest.algorithm` ist ausschliesslich `hmac-sha-256`. Der Key besitzt mindestens 256 zufaellige Bit, liegt in einem Hub-only Secret-Provider und ist ausschliesslich fuer den Non-Media-Scope `hub-only-non-media` zugelassen. Media-, E2EE-, Signatur-, Tenant- oder Receiver-Keys sind nicht substituierbar. Keymaterial wird weder persistiert noch an SFU oder Worker uebertragen; `key_ref` benennt nur den Hub-internen Keyslot.

Der kanonische Digest-Input ist ein neues JSON-Objekt mit exakt diesen Feldern aus dem Snapshot:

```json
{
  "created_at": "<created_at>",
  "digest_binding": {
    "algorithm": "<member_digest.algorithm>",
    "domain": "<member_digest.domain>",
    "encoding": "<member_digest.encoding>",
    "key_ref": "<member_digest.key_ref>",
    "key_scope": "<member_digest.key_scope>",
    "key_version": "<member_digest.key_version>"
  },
  "epochs": "<epochs>",
  "expires_at": "<expires_at>",
  "hub_audience": "<hub_audience>",
  "limits": "<limits>",
  "lineage": "<lineage>",
  "projection_version": "<projection_version>",
  "schema": "<schema>",
  "scope": "<scope>",
  "snapshot_id": "<snapshot_id>",
  "ttl_seconds": "<ttl_seconds>"
}
```

Die Platzhalter stehen fuer die typisierten JSON-Werte, nicht fuer Strings. `immutable`, `member_digest.value` und das redundante `sfu_projection` sind nicht Teil des Inputs.

Die kanonische Serialisierung wird rekursiv wie folgt gebildet:

1. Objektschluessel werden nach ihren unsigned UTF-8-Bytes strikt aufsteigend sortiert.
2. Es gibt kein Whitespace ausserhalb von JSON-Strings.
3. Strings verwenden JSON-Doppelquotes und die kuerzeste erforderliche JSON-Escapesequenz; Schemawerte dieses Vertrags sind ASCII.
4. Integer werden dezimal ohne Pluszeichen und ohne fuehrende Nullen serialisiert.
5. `null` wird als die vier ASCII-Bytes `null` serialisiert.
6. Arrays behalten ihre Reihenfolge; die zuvor beschriebene semantische Sortierpruefung muss davor erfolgreich sein.

Die HMAC-Nachricht ist:

```text
ASCII("ananta.webrtc.audience-snapshot.member-digest.v1") || 0x00 || UTF8(canonical_input)
```

Der 32-Byte-HMAC-Ausgang wird ungepaddet als Base64url in `member_digest.value` gespeichert. `sfu_projection.audience_digest` muss diesem Wert in konstanter Zeit entsprechen. Ein unkeyed SHA-256, ein vom Client gelieferter Digest oder ein Digest ueber bloss sortierte Member-IDs ist ein Dictionaryangriff und wird abgelehnt.

## Zeit- und Mengengrenzen

Das Schema setzt absolute Hard Caps:

| Grenze | Hard Cap |
|---|---:|
| Mitglieder | 8 |
| Grants pro Mitglied | 8 |
| kanonische Snapshotgroesse | 32768 Bytes |
| TTL | 300 Sekunden |

`limits` bindet die zum Erstellungszeitpunkt wirksamen Hub-Konfigurationswerte in den Digest ein. Konfiguration darf die Hard Caps nur senken. Semantisch gilt:

- `member_count <= limits.members_max`
- `grant_count_per_member <= limits.grant_refs_per_member_max`
- `canonical_snapshot_bytes <= limits.snapshot_bytes_max`
- `ttl_seconds <= limits.ttl_seconds_max`
- `expires_at - created_at == ttl_seconds`

Die Bytegrenze gilt fuer die kanonische UTF-8-Serialisierung des vollstaendigen Snapshots, nicht fuer komprimierte Transportbytes. Ein fehlendes Limit, eine unlesbare Config oder ein Ueberlauf fuehrt zur Ablehnung.

## Fail-closed Validierungsreihenfolge

Implementierungen verwenden diese Reihenfolge, damit negative Fixtures einen stabilen primaeren Reason-Code besitzen:

| Reihenfolge | Pruefung | Reason-Code |
|---:|---|---|
| 1 | Parser, Schema, Algorithmus und absolute Hard Caps | `audience_schema_invalid`, bei unkeyed/fremdem Algorithmus `audience_digest_algorithm_unsupported` |
| 2 | sortierte und eindeutige Member-, Handle- und Grantreferenzen | `audience_duplicate_grant_ref`, `audience_noncanonical_order` |
| 3 | immutable Lineage, neue ID und monotone Version | `audience_lineage_invalid` |
| 4 | kanonische Hub-Aufloesung und Scope | `audience_cross_tenant_grant`, `audience_cross_room_grant`, `audience_cross_publication_grant` |
| 5 | Membership, Consent, Epochen, Grant-Version und Grant-Gueltigkeit | `audience_stale_grant`, `audience_grant_inactive`, `audience_authority_mismatch` |
| 6 | effektive Member-, Byte- und TTL-Limits | `audience_members_limit_exceeded`, `audience_snapshot_bytes_limit_exceeded`, `audience_ttl_limit_exceeded` |
| 7 | Handle-/Digest-Reuse im Retention-Index | `audience_cross_room_linkability`, `audience_cross_publication_linkability`, `audience_cross_key_epoch_linkability` |
| 8 | HMAC-Neuberechnung und konstanter Vergleich | `audience_digest_mismatch` |
| 9 | exakte minimale SFU-Projektion | `audience_sfu_projection_mismatch` |

Keine spaetere erfolgreiche Pruefung darf einen frueheren Fehler ueberstimmen. Logs enthalten Reason-Code, Snapshot-ID und opaque Scope-Referenzen, aber keine Member-, Consent- oder Grantlisten und kein Keymaterial.

## Fixture-Korpus

`tests/fixtures/webrtc/audience_snapshot/manifest.json` beschreibt den deterministischen Korpus. Der dortige HMAC-Key ist ausschliesslich Testmaterial und fuer Produktion explizit unbrauchbar. `state_profiles` simulieren den kanonischen Hub-Zustand; sie sind keine `SRC_*`-/`RUN_*`-Evidence und begruenden keine Produktionsaussage.

Positive Dateien muessen Schema- und semantische Validierung bestehen. Negative Dateien koennen absichtlich schema-gueltig sein und erst gegen den angegebenen State-/History-Kontext scheitern. Ein Conformance-Runner muss beide Ebenen pruefen und exakt den angegebenen primaeren Reason-Code liefern.

## Source-grounded Grenze

Der Vertrag behauptet keine Vendor-, Deployment- oder Laufzeitfaehigkeit. Solche Aussagen und jede Aktivierungsentscheidung benoetigen separat bereitgestellte, gueltige `SRC_*`- beziehungsweise `RUN_*`-Evidence. Fehlende Evidence bleibt unverified und aktiviert Broadcast-Fanout nicht.
