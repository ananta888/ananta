# Fanout Route Intent v1

## Zweck und Autoritaet

Das Schema "schemas/webrtc/fanout_route_intent.v1.json" beschreibt eine
kurzlebige, absolute Route-Projektion des Hubs fuer genau eine Publication.
Der Hub bleibt alleiniger Eigentuemer von Audience, Group, Policy, Routing,
Epochen, Fencing, Runtime-Auswahl und Reconciliation. Die SFU fuehrt nur die
vom Hub signierte Projektion aus. Sie darf keine Receiver, Publications,
Layer, Traffic-Klassen oder Laufzeit verlaengern oder ableiten.

Der Vertrag ist additiv und aendert weder das Hub-Worker-Modell noch
LiveKits native Placement-Verantwortung. Worker erzeugen und delegieren keine
Route-Intents. Ein Hub-delegierter Worker darf hoechstens ein begrenztes
Ergebnis oder Evidence an den Hub zurueckgeben.

## Signatur- und Parent-Grenze

Das Feld "envelope" referenziert direkt den kanonischen Parent
"https://ananta.local/schemas/webrtc/secure_envelope.v1.json". Es gibt kein
zweites Signature-, Key-, Nonce-, Sequence-, Replay- oder Expiry-Feld. Mit
"Hub-Signatur" bezeichnet dieser Vertrag ausschliesslich die erfolgreiche
kryptographische Authentizitaetspruefung des Parent-Envelopes gegen einen
tenant- und roomgebundenen vertrauenswuerdigen Hub-Key.

Nach erfolgreicher Parent-Pruefung muss der entschluesselte authentifizierte
Plaintext bytegenau der kanonischen JSON-Darstellung des Root-Dokuments ohne
"envelope" entsprechen. Der Parent-Contract-Digest im AAD muss den exakten
Schema-/Semantikstand dieses Vertrags bezeichnen. Zusaetzlich gilt:

- "envelope.sender_id" muss ein vertrauenswuerdiger Hub-Sender sein.
- "envelope.scope.kind" ist "room" und seine ID entspricht
  "route.scope.room_ref".
- "envelope.recipient.kind" ist "group" und seine ID entspricht
  "route.group.group_ref".
- "envelope.epoch" entspricht "route.epochs.key_epoch".
- "envelope.expires_at_ms" entspricht
  "route.issued_at_ms + route.ttl_ms".
- "envelope.payload_type" und "domain" sind exakt
  "sfu_broadcast.fanout_route_intent.v1".

Eine syntaktisch gueltige Envelope oder Key-ID beweist keine Signatur. Fehlende
Truststore-Aufloesung, fehlgeschlagene Authentizitaet, Plaintextabweichung,
Nonce-/Sequence-Reuse oder ein bereits akzeptierter Intent werden fail-closed
abgelehnt.

## Gebundener Scope

Jeder Intent bindet unveraenderlich:

| Bereich | Bindung |
| --- | --- |
| Route | "route_id" und ausschliesslich "absolute_replace" |
| Scope | Tenant und Room |
| Group | Group-Referenz, Revision und publication-spezifischer Member-Digest |
| Audience | Snapshot-Referenz, Projektionsversion, Audience-Digest und opaque Receiver-Handles |
| Runtime | Control-Mode, Placement-Owner, Cluster und Region |
| Publication | genau eine Publication, Media-Kind und exakte erlaubte Layer-Tupel |
| Traffic | Gesamtbitrate und geschlossene per-Class Obergrenzen |
| Epochs | Route-, Topology- und Media-Key-Epoch |
| Fencing | Token, erwartete Vorgaenger-Route-Epoch und Enforcement-Modus |
| Zeit | Issued-at, Parent-Expiry und TTL |
| Sicherheit | Parent-Hub-Authentizitaet, Sequence, Nonce, AAD und Contract-Digest |

Group- und Audience-Referenzen werden gegen die immutable, Hub-persistierten
CON-002-/CON-009-Artefakte aufgeloest. Die Route transportiert weder Member-,
Grant-, Consent-, Rollen-, Subscription- noch Keylisten. Receiver-Handles sind
Snapshot-spezifisch, opaque und nicht als Participant-ID interpretierbar.

## Geschlossene Runtime-Modi

"runtime_control_mode" kennt im ausfuehrbaren Route-Vertrag genau zwei Modi.
"unsupported" erzeugt nie einen Route-Intent und liefert
"route_runtime_unsupported".

### livekit_control_api

Der Hub bindet nur Cluster und Region. "placement_owner" ist zwingend
"livekit_native". "runtime_instance_ref", Node-ID, Node-ACK und
Runtime-CAS-Evidence sind in diesem Schema-Zweig nicht erlaubt.

Das Fencing-Token ist nur eine Hub-seitige Serialisierungs- und
Reconciliation-Bindung. Es darf gegenueber der Stock-LiveKit-API weder als
serverseitige Token-Precondition verlangt noch als bestaetigtes Node-Fencing
dargestellt werden. LiveKit waehlt den konkreten Node.

### authenticated_runtime_extension

Eine konkrete "runtime_instance_ref" ist nur in diesem Modus erlaubt. Der
Intent muss mindestens eine "runtime_capability_evidence_refs" enthalten und
die schmale Port-Version
"ananta.sfu.authenticated-runtime-route.v1" binden. Der Hub-Verifier muss jede
Referenz gegen tatsaechlich bereitgestellte, gueltige "SRC_*"- oder
"RUN_*"-Evidence aufloesen und deren Runtime-, Cluster-, Region-, Port-,
Fencing- und Gueltigkeitsscope vergleichen.

Das Schema prueft nur die Form einer Evidence-ID. Es beweist weder Existenz
noch Gueltigkeit. Eine unbekannte, fehlende, abgelaufene oder scopefremde ID
liefert "route_runtime_evidence_unverified". Ohne solche Evidence bleibt der
Extension-Zweig unausfuehrbar. Der Fixture-Korpus erfindet deshalb keine
Source-ID und enthaelt keinen positiven Extension-Claim.

## Keine Rechteerweiterung

"projection" ist immer "absolute_replace". Delta-, Patch-, Selector-, Range-,
Wildcard- und Default-Allow-Semantik sind verboten.

- Nur die in "audience.receiver_refs" enthaltenen Handles duerfen Ziel sein.
- Nur "publication.publication_ref" darf weitergeleitet werden.
- Nur die vollstaendig aufgezaehlten Layer-Tupel duerfen verwendet werden.
- Eine nicht aufgezaehlte Traffic-Class besitzt ein Budget von null.
- Jedes per-Class-Limit und das Aggregate-Limit sind harte Obergrenzen.
- Der frueheste Ablauf aus Parent, Hard Cap und aufgeloester Config gilt.
- Eine leere, unbekannte, stale oder widerspruechliche Aufloesung wird nicht
  repariert, erweitert oder auf einen bequemeren Scope umgebogen.

Layer sind exakte Tupel. Bei Video bilden "rid", "spatial_id" und
"temporal_id" keine obere Range, sondern genau eine erlaubte Kodierung. Ein
niedrigeres oder hoeheres nicht aufgelistetes Tupel ist ebenfalls verboten.
Audio besitzt ausschliesslich Layer 0/0.

## Harte und konfigurierbare Limits

Die absoluten v1-Hard-Caps lauten:

| Grenze | Hard Cap |
| --- | ---: |
| komplettes UTF-8-Intent vor Parsing | 32768 Bytes |
| authentifizierter kanonischer Plaintext | 16384 Bytes |
| Receiver-Referenzen | 7 |
| Layer pro Publication | 3 |
| Traffic-Klassen | 4 |
| TTL | 10000 ms |
| Bitrate pro Klasse / aggregiert | 100000000 bit/s |
| Pakete pro Klasse | 100000/s |
| Burst pro Klasse | 1048576 Bytes |

"limits.config_refs" bindet stabile Namen. Der Hub loest sie aus seiner
autoritativen, gemessenen und tenantfaehigen Konfiguration auf. Der effektive
Wert ist jeweils das Minimum aus Schema-Hard-Cap, aufgeloestem Configwert,
Parent-Limit und einem engeren Publication-/Admission-Budget. Config darf
Hard-Caps nur senken. Fehlende, stale, nicht positive oder unpassende
Aufloesung liefert "route_limit_unresolved".

Der Parser prueft die rohe Bytegrenze vor JSON-Parsing. Danach werden
Array-/String-/Zahlen-Hard-Caps geprueft und erst dann kanonische Bytes
allokiert. Die Summe aller per-Class-Bitraten darf das Aggregate nicht
ueberschreiten. Traffic-Klassen, Receiver-Handles und Layer-Tupel muessen
jeweils eindeutig sein.

## Fail-closed Validierungsreihenfolge

Die erste fehlschlagende Stufe bestimmt den stabilen Reason-Code:

| Stufe | Pruefung | Primaere Codes |
| ---: | --- | --- |
| 1 | rohe Bytes, Parser, geschlossenes Schema, Hard-Caps | "route_intent_bytes_hard_limit_exceeded", "route_schema_invalid" |
| 2 | exakter Parent-Ref, Domain, Hub-Truststore und Authentizitaet | "route_parent_ref_mismatch", "route_domain_mismatch", "route_hub_signature_invalid" |
| 3 | Parent-Scope, Recipient, Plaintext, Ablauf und Replay | "route_cross_room", "route_group_recipient_mismatch", "route_signed_payload_mismatch", "route_expired", "route_replay" |
| 4 | Config-/Admission-Limits | "route_limit_unresolved", "route_effective_limit_exceeded" |
| 5 | Runtime-Modus und belegte Runtime-Capability | "route_cross_runtime", "route_cross_cluster", "route_cross_region", "route_runtime_evidence_unverified" |
| 6 | Group-/Audience-/Publication-Aufloesung und Digests | "route_group_digest_mismatch", "route_audience_digest_mismatch", "route_cross_publication", "route_receiver_not_in_snapshot" |
| 7 | Epochen und Fencing | "route_stale_route_epoch", "route_stale_topology_epoch", "route_stale_key_epoch", "route_stale_fencing" |
| 8 | exakte Layer- und Traffic-Schnittmenge | "route_layer_not_authorized", "route_traffic_budget_exceeded" |
| 9 | atomare Hub-CAS-Persistenz und Dispatch | "route_cas_conflict", "route_runtime_unsupported" |

Der Hub prueft alle Referenzen tenant- und roomgebunden aus seinen
autoritativen Repositories. Erst nach vollstaendiger Validierung persistiert
er Route-ID, Epochs, Fencing, Parent-Sequence und Intent-Digest atomar. Der
Runtime-Adapter erhaelt danach eine bereits eingeschraenkte Desired-State-
Projektion. Ein Adapter darf weder Policy evaluieren noch einen Worker oder
anderen Runtime-Adapter aufrufen.

## Deterministische Fixtures

Der Korpus unter
"tests/fixtures/webrtc/fanout_route_intent/" verwendet einen vollstaendigen
Native-Basisfall und JSON-Pointer-Mutationen. "validation_context" ist
deterministischer Testzustand, keine Production-Evidence. Mutationen werden
gegen "instance" ausgefuehrt, ausser "target" nennt
"validation_context".

Der Korpus deckt Minimal-/Maximalfall, Unknown Property, Bytegrenzen,
Receiver-/Layer-/Traffic-/TTL-Hard- und Effective-Limits, erfundene Native-
Nodebindung, Native-Tokenprecondition, fehlende Runtime-Evidence,
Cross-Runtime/-Cluster/-Region/-Room/-Publication, Digestmanipulation,
Receiver-/Publication-/Layer-/Traffic-Erweiterung, stale Route/Topology/Key/
Fencing, Ablauf, Replay und Parent-Authentizitaet ab.

## Source-grounded und Release-Grenze

Dieser Vertrag und seine Testfixtures sind keine Aussage ueber Vendor-,
Deployment-, Browser-, Capacity- oder Release-Faehigkeit. Es wurden fuer
diesen Vertrag keine gueltigen "SRC_*"- oder "RUN_*"-IDs bereitgestellt.
Parent-"no_go"/"observe_only" bleibt daher unveraendert fail-closed. Weder
Schemaexistenz noch eine syntaktisch passende Evidence-ID darf Broadcast
aktivieren.

## SOLID-Pruefung

- SRP: Schema/Validator, Hub-Policy, Persistenz und Runtime-Adapter bleiben
  getrennte Verantwortungen.
- OCP/DIP: Der geschlossene Runtime-Port erlaubt eine belegte Extension, ohne
  LiveKit-Native-Placement oder zentrale Hub-Logik umzubauen.
- LSP: "unsupported" und unbelegte Capabilities liefern Fehler statt
  synthetischem Erfolg.
- ISP: Der Route-Intent transportiert nur die minimale Route-Projektion, keine
  Health-, Membership-, Consent-, Key- oder Task-API.
- Hidden side effects: Ausfuehrung erfolgt erst nach Hub-CAS; die SFU kann aus
  fehlenden Feldern keine Default-Rechte ableiten.
