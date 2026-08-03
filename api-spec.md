# Ananta Agent API Spezifikation

Diese Dokumentation beschreibt die API-Endpunkte des Ananta Agenten.
Die kanonische maschinenlesbare Spezifikation wird jetzt zentral aus dem Contract-Katalog erzeugt und ist unter `/api/system/openapi.json` verfuegbar.

## Basis-URL
`http://<agent-ip>:<port>` (Standard-Port: 5000)

## Authentifizierung
Die API verwendet Bearer-Tokens zur Authentifizierung. Eine detaillierte Übersicht über die Mechanismen und Middleware finden Sie in der [Authentifizierungs-Dokumentation](docs/api_auth_overview.md).
Der Token muss im `Authorization` Header gesendet werden:
`Authorization: Bearer <dein-token>`

---

## Minimalbeispiele fuer den Einstieg

Diese Beispiele decken die haeufigsten Integrationsfaelle ab, ohne die ganze API kennen zu muessen.

### 1. Login und Token holen

```bash
TOKEN=$(curl -s http://localhost:5000/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}' | jq -r '.data.access_token')
```

### 2. Ziel planen und Tasks erzeugen

```bash
curl -s http://localhost:5000/goals \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Analysiere dieses Repository und schlage naechste Schritte vor","create_tasks":true}'
```

### 3. Board/Tasks lesen

```bash
curl -s http://localhost:5000/tasks \
  -H "Authorization: Bearer $TOKEN"
```

### 4. Ergebnisartefakte lesen

```bash
curl -s http://localhost:5000/artifacts \
  -H "Authorization: Bearer $TOKEN"
```

### 5. Einfacher Webhook fuer externe Tools

```bash
curl -s http://localhost:5000/triggers/webhook/generic \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Build fehlgeschlagen","description":"Frontend build failed in CI","priority":"high"}'
```

Der Hub bleibt dabei die Steuerungsebene: Externe Systeme liefern Ziele oder Ereignisse, die Ausfuehrung laeuft weiter ueber Tasks und Worker.

---

## Enterprise-Organisationen (Hub Control Plane)

Alle Endpunkte in diesem Abschnitt laufen ausschliesslich am Hub. Definitionen
werden aus dem validierten Produktionskatalog und optionalen projektgebundenen
Revisionen gelesen; `test_only_fixtures` werden nie als Produktions-Blueprint
ausgeliefert. Die Standardauswahl umfasst 5 bis 10 Teams, mit 8 Teams als
empfohlener mittlerer Zusammensetzung. Worker koennen Rollenaufgaben und
geschlossene Task-Vorschlaege liefern, orchestrieren oder adressieren aber
keine anderen Worker; Einordnung und Zuweisung bleiben Hub-Aufgabe.

Jede Antwort verwendet den normalen Envelope `{"status":"success","data":...}`.
Tenant, Projekt und Principal stammen aus dem authentifizierten Request-Kontext
beziehungsweise einer explizit autorisierten Projektauswahl, niemals aus einem
vom Client behaupteten Compile-Plan. Bei mehreren autorisierten Projekten muss
`project_id` explizit angegeben werden.

### Definitionen lesen und kompilieren

- `GET /api/organization-blueprints?page_size=100&cursor=...`
  liefert `{items, next_cursor}`. Die `key`-Werte der Standardvarianten haben
  die Form `<definition-key>:standard:<team-count>`.
- `GET /api/organization-blueprints/<key>?version=1` liefert die portable,
  prompt-freie Definition samt kanonischer `revision`.
- `POST /api/organization-blueprints/<key>/admission-exceptions` stellt eine
  produktive, einmal verwendbare Freigabe fuer eine exakte Custom-N-
  Zusammensetzung aus. Der Aufruf erfordert `ProjectCapability.MANAGE` und
  `Idempotency-Key`. Body:

  ```json
  {
    "blueprint_version": "1",
    "team_blueprint_counts": {
      "enterprise_product_delivery_scrum": 2,
      "portfolio_product_coordination": 1,
      "platform_devops_sre": 1
    },
    "reason": "Bewusst reduzierte, projektgebundene Startkomposition",
    "ttl_seconds": 900
  }
  ```

  `ttl_seconds` liegt zwischen 60 und 3600 Sekunden; ohne Angabe gelten 900
  Sekunden. Der Hub validiert bekannte Team Blueprints, Singleton-/Gruppen-
  Kardinalitaeten, mindestens zwei Teams und das aktuelle Projektlimit. Die
  Antwort (`201`, identischer Idempotency-Replay `200`) enthaelt
  `admission_exception_ref`, `definition_ref`, `definition_revision`,
  `composition_digest`, normalisierte `team_blueprint_counts`, `team_count`,
  `capability_gaps`, `policy_hash`, `status=issued`, `expires_at` und
  `replayed`. Derselbe Idempotency-Key mit veraendertem Body ist ein Konflikt.

  Die Freigabe ist an Tenant, Projekt, Principal, Definition-Key/-Version,
  kanonische Definitionsrevision, aktuellen Limit-Policy-Hash und exakte
  Counts gebunden. Ein anderer Scope/Principal, geaenderte Counts, eine neue
  Definition/Policy, Ablauf, Widerruf oder vorheriger Verbrauch machen sie
  ungueltig. Der Endpoint startet weder Compile noch Instanziierung und erzeugt
  keine Tasks oder Worker-Aktivitaet.
- `POST /api/organization-blueprints/<key>/compile` ist ein reiner Dry-Run.
  Der Body entspricht:

  ```json
  {
    "blueprint_key": "enterprise_agentic_scrum:standard:8",
    "blueprint_version": "1",
    "title": "Produktorganisation Alpha",
    "team_count": 8,
    "custom_team_blueprint_keys": [],
    "admission_exception_ref": null,
    "parameters": {}
  }
  ```

  Der Compile-Plan enthaelt unter anderem `organization_id`,
  `definition_revision`, `plan_digest`, einen kurzlebigen `compile_token`,
  `admin_policy_hash`, geplante Schreibmengen, Diagnosen und wirksame Limits.
  Freie Zusammensetzungen sind fail-closed und benoetigen eine projektgebundene
  Admission-Exception. Fuer Custom N enthaelt `parameters` die exakt bei der
  Ausnahme freigegebenen `team_blueprint_counts`; `admission_exception_ref`
  transportiert nur deren opaken Bezug. Compile validiert die noch als
  `issued` markierte Ausnahme lesend und verbraucht sie nicht. Der Compile-Token
  ist an Tenant, Projekt, Principal,
  Organisations-ID, Definition, Zusammensetzung, Digest, Policy-Hash und Titel
  gebunden.
- `POST /api/organization-blueprints/<key>/precreation-admin-grants`
  akzeptiert den unveraenderten `compile_plan`, kompiliert ihn serverseitig im
  authentifizierten Projekt erneut und verlangt
  `If-Match: "<definition_revision>"`, `Idempotency-Key` sowie
  `ProjectCapability.MANAGE`. Erst danach stellt der Hub einen 60–3600 Sekunden
  gueltigen `instantiate`-Grant aus, der exakt an Principal, Plan-Digest und
  aktuellen Policy-Hash gebunden ist. Der Endpoint instanziiert nichts.
  Derselbe `Idempotency-Key` liefert nur denselben noch gueltigen Grant;
  abgelaufene oder verbrauchte Grants bleiben unter diesem Key terminal. Ein
  neuer Key darf fuer denselben unveraenderten Plan einen neuen One-shot-Grant
  ausstellen.

### Definitionen erstellen, revisionieren, archivieren und reconciliieren

Projektgebundene Revisionen liegen vor dem unveraenderlichen Standardkatalog;
fehlt eine solche Revision, bleibt die validierte Datei-Definition der
produktive Fallback. Alle folgenden Endpunkte erfordern
`ProjectCapability.MANAGE`. Eine Mutation ersetzt nie den Inhalt einer
Revision und veraendert keine laufenden Instanz-Snapshots.

- `POST /api/organization-blueprints/validate` und
  `POST /api/organization-blueprints/<key>/validate` validieren eine
  `definition`, deren naechste Version, Referenzabschluss und wirksames
  Limitprofil. `lifecycle` ist `draft` oder `active`; optional bindet
  `expected_parent_revision` die Vorschau an den aktuellen Parent. Der Aufruf
  benoetigt `Idempotency-Key` und liefert `mutation_digest`, `policy_hash`,
  `parent_revision`, echte `referenced_definition_hashes` sowie einen
  kurzlebigen, principal- und projektgebundenen `admin_grant`. Der
  `mutation_digest` bindet Definition, Parent, Lifecycle, aktuellen Policy-Hash
  und die transitive Referenz-Closure aus Teams, Rollen, Workflows, Handoffs
  und Policies.
- `POST /api/organization-blueprints` legt Version 1 an. Es verlangt
  `If-Match: "none"`, `Idempotency-Key`, den unveraenderten
  `mutation_digest` sowie denselben Grant in
  `X-Organization-Admin-Grant`. Optional akzeptiert `admin_grant` im Body
  entweder dieselbe Grant-ID oder das unveraenderte Vorschauobjekt; massgeblich
  bleibt dessen `grant_id`, die exakt zum Header passen muss.
- `PATCH /api/organization-blueprints/<key>` und der semantisch identische
  additive Endpoint `POST /api/organization-blueprints/<key>/revisions`
  erzeugen ausschliesslich die naechste immutable Revision. `If-Match` ist
  exakt die `parent_revision` der Validation; In-place-Patches gibt es nicht.
- `POST /api/organization-blueprints/<key>/archive-preview` akzeptiert
  `version`, meldet nicht archivierte Instanzen als Blocker und stellt nur bei
  einer anwendbaren Vorschau einen plan-gebundenen Grant aus.
  `POST /api/organization-blueprints/<key>/archive` verbraucht ihn atomar und
  verlangt `If-Match: "<revision>"`, `mutation_digest` und
  `Idempotency-Key`. Eine entfernte Datei-Definition wird durch einen
  projektgebundenen `retired`-Marker verdeckt, niemals aus dem Seed geloescht.
- `POST /api/organization-blueprints/<key>/reconcile-preview` vergleicht
  `current_version` entweder mit `source: "seed"` oder einer expliziten
  `desired_definition`. Die Antwort enthaelt Abschnitts-Drift,
  `entity_drift` je Unit/Gruppe/Role Slot/Workflow/Relation/Policy/Referenz,
  betroffene laufende `assignment_impacts`, erhaltene
  `preserved_snapshot_revisions`, lokale Override-Konflikte,
  `planned_writes`, `plan_digest`, `applicable` und `requires_apply`.
- `POST /api/organization-blueprints/<key>/reconcile-apply` akzeptiert die
  unveraenderte komplette `preview`, den Vorschau-Grant und
  `If-Match: "<current_revision>"`. Der Hub berechnet Vorschau,
  Definitions-/Policy-Hashes und aktuelle Version erneut, verbraucht den
  One-shot-Grant in derselben Transaktion und legt genau eine neue Revision
  an. Ein No-op, ein lokaler Override-Konflikt oder eine stale Vorschau wird
  abgewiesen.

Der Seed-Start validiert in deterministischer Reihenfolge Template-Fragmente,
Team Blueprints und danach Organization Blueprints. Standarddefinitionen
bleiben file-backed; Seed-Reconcile erfolgt nur nach Vorschau und explizitem
projektgebundenem Apply. Keiner dieser Definition-Endpunkte kompiliert
Runtime-Lifecycle-Aktionen, startet Worker oder schreibt Tasks.

### Instanzen und Lifecycle

- `GET /api/organizations?page_size=50&cursor=...` liefert nur Organisationen,
  fuer die der Principal eine aktive Membership im autorisierten Projekt hat.
- `GET /api/organizations/<organization-id>` liefert das Summary inklusive
  `definition_revision`, `snapshot_hash`, Team-/Unit-Anzahl und `lock_version`.
- `POST /api/organizations` materialisiert atomar einen aktuellen Compile-Plan.

  Erforderliche Header:

  ```text
  If-Match: "<definition_revision>"
  Idempotency-Key: <mindestens 8 nicht-leere Zeichen>
  X-Organization-Admin-Grant: <plan-bound one-shot grant id>
  ```

  Body:

  ```json
  {
    "compile_plan": {"compile_token": "...", "definition_revision": "...", "plan_digest": "..."},
    "title": "Produktorganisation Alpha",
    "admin_grant": "grant-id-identisch-zum-header"
  }
  ```

  Direkt vor dem Schreiben kompiliert der Hub aus dem signierten Binding und
  seinem aktuellen Katalog erneut. Clientseitige Units, Rollen, Tenant- oder
  Projektwerte werden nicht uebernommen. Der Precreation-Grant muss exakt zu
  Tenant, Projekt, Principal, Plan-Digest und aktuellem Admin-Policy-Hash
  passen, darf weder abgelaufen noch widerrufen sein und wird durch ein
  bedingtes Update in derselben Transaktion einmalig verbraucht. Rollt die
  Materialisierung zurueck, rollt auch der Verbrauch zurueck. Ein identischer
  erfolgreicher Idempotency-Replay liefert das bestehende Ergebnis, ohne den
  bereits verbrauchten Grant erneut zu fordern.

  Bei Custom N wird zusaetzlich die im Compile-Token gebundene
  `admission_exception_ref` gegen denselben Principal/Scope und den aus
  Definition, Definitionsrevision, aktuellem Policy-Hash und Counts erneut
  berechneten `composition_digest` geprueft. Der Statuswechsel
  `issued -> consumed`, der Verbrauch des Precreation-Admin-Grants und alle
  Instanz-Writes teilen eine Unit of Work. Ein Rollback stellt daher auch die
  Ausnahme wieder als unverbraucht her. Nach erfolgreichem Commit kann die
  Ausnahme keine zweite Instanz autorisieren; nur der identische erfolgreiche
  Instantiate-Idempotency-Replay darf das vorhandene Ergebnis wiedergeben.

  Die Erfolgsantwort enthält zusätzlich `organization_admin_grant_id` für den
  neu erzeugten, organisationsgebundenen und widerrufbaren Grant des
  Erstellers. Clients dürfen diesen Wert nur im geschützten Sitzungszustand
  halten und weder protokollieren noch in URLs, Shell-History oder
  Präsentationszustand übernehmen. Ein identischer Idempotency-Replay liefert
  denselben Grant-Bezug statt einen zweiten Grant auszustellen.

- `POST /api/organizations/<organization-id>/lifecycle` verwendet einen
  organisationsgebundenen Admin-Grant und folgende Header:
  `If-Match: "<lock_version>"`, `Idempotency-Key` und
  `X-Organization-Admin-Grant`. Body:

  ```json
  {"target_state":"paused","active_work_strategy":null,"admin_grant":"grant-id"}
  ```

  Fuer `active_work_strategy=migrate` ist zusaetzlich ein vollstaendig
  gescoptes Ziel erforderlich:

  ```json
  {
    "target_state": "completed",
    "active_work_strategy": "migrate",
    "migration_target": {
      "organization_id": "validated-successor-id",
      "unit_id": "target-team-unit-id",
      "team_id": "target-team-id",
      "role_slot_id": "target-role-slot-id"
    },
    "admin_grant": "grant-id"
  }
  ```

  Erlaubte Zustande sind `draft`, `validated`, `active`, `paused`, `completed`
  und `archived`. Archivieren loescht keine Lineage. Der Hub liest aktive
  Tasks, Leases, Gates und Handoffs innerhalb derselben Transaktion erneut und
  materialisiert Drain, Cancel oder die explizit zielgebundene Migration vor
  dem Zustandswechsel. Migration erzeugt deterministische Successor-Tasks mit
  `source_task_id`; sie verschiebt niemals still eine bestehende Task-ID.
  Offene Leases, Jobs, Gates und Handoffs werden strukturiert aufgeloest und
  im Lifecycle-Ergebnis referenziert. Recovery `archived -> validated` startet
  weder Worker noch bestehende Tasks neu.

### Persistenter Organization-Runtime

- `GET /api/organizations/<organization-id>/runtime?event_limit=500` liefert
  die an `definition_revision` und `snapshot_hash` gebundene Eventprojektion,
  autoritative Task-/Dependency-Zustaende, hierarchische Budgetnutzung,
  Cross-Team-Handoffs und begrenzte Workflow-Loop-Zustaende. `event_limit`
  liegt zwischen 1 und 1000; eine gekuerzte Eventliste ist markiert. Prompt-
  Bodies, Secrets und Artifact-Inhalte sind nie Teil der Antwort.
- `POST /api/organizations/<organization-id>/handoffs` ist ein vom Hub
  vermittelter, projektgescopter CAS-Write. Er verlangt `Idempotency-Key` und
  bindet Producer/Consumer jeweils an Unit, Team, Role-Slot und Task, dazu
  Goal, eine versionierte Handoff-Definition, strukturierte
  Artifact-ID/Version/Digest-Referenzen, Acceptance Checks, Due/SLA,
  Assignment und Dispatch-Lease. `grounding` nennt die versionierte Policy,
  die bereitgestellten `SRC_####`-/`RUN_####`-Allowlisten und die daraus
  verwendeten Evidence-Refs; `verification_status` muss `hub_verified` sein.
  Der Hub gleicht die Handoff-Definition mit der aktiven Organization-Relation
  ab, liest Goal-Mitgliedschaft/Verifikation aus dem bestehenden
  Goal-Artifact-Graph und den unveraenderlichen Digest aus der bestehenden
  Artifact-Version. Nur identische Digests, exakte Evidence-Allowlist- und
  explizite Context-Scope-Freigaben werden akzeptiert.
- `POST /api/organizations/<organization-id>/handoffs/<handoff-id>/decisions`
  verlangt `If-Match: "<handoff-revision>"`, `Idempotency-Key`, eine aktive
  Consumer-Assignment des authentifizierten Principals sowie `accepted`,
  `rejected` oder `needs_changes` mit strukturiertem `reason_code`.

Budget-Reservation/Settlement und Workflow-Loop-Erzeugung sind absichtlich
keine frei beschreibbaren HTTP-Schreibendpunkte. Der Hub ruft den
`OrganizationDispatchBudgetPort` mit bereits autoritativ aufgeloesten Limits
auf und erzeugt Loop-State aus versionierten Workflow-Definitionen. Worker,
Model/Cost-Stewards und Clients koennen Limits oder Provider-Policy dadurch
nicht selbst erhoehen.

### Hierarchie, Graph, Rollen und Layout

- `GET /api/organizations/<id>/topology` unterstuetzt `cursor`, `page_size`,
  `depth`, `subgraph_root_id`, `kinds`, `edge_namespaces`, `search` und
  `include_runtime`. Die Antwort entspricht `OrganizationTopologyPage` mit
  stabilen Nodes, getrennten Namespaces `hierarchy`, `organization`, `runtime`,
  Revisionsbindung, Limits und `next_cursor`. `depth=0` liefert nur den
  Organisationsknoten; die erste Unit-Ebene beginnt bei `depth=1`.
- `GET /api/organizations/<id>/role-slots` liefert Rollen-Template, Scrum-
  Accountability, Spezialisierung, Capabilities, SoD-Risiko und Zuweisungen.
- `GET /api/organizations/<id>/role-slots/<slot-id>/assignment-candidates`
  liefert nur redigierte Agenten-Metadaten, Capability-Kompatibilitaet,
  Kapazitaet, betroffene Teams und strukturierte Gruende. Tokens werden nie
  ausgegeben.
- `GET|PUT /api/organizations/<id>/layout-preferences` liest/speichert
  ausschliesslich benutzerspezifische Koordinaten fuer `hierarchy` oder
  `graph`. Das idempotente PUT ist die einzige bewusste Ausnahme von
  Admin-Grant/If-Match bei Organization-Schreibendpunkten: Layout ist
  nicht-autoritatives Presentation State und kann Topologie, Rollen,
  Zuweisungen, Lifecycle oder Policy nicht veraendern.

### Topologie-Patches

- `POST /api/organizations/<id>/patches/preview` akzeptiert einen geschlossenen
  Vertrag `{expected_revision, operations}`. Erlaubte Operationen sind
  `add`, `remove`, `reparent`, `connect` und `assign`; Runtime-Kanten sind
  niemals schreibbar. `If-Match` muss der Body-Revision entsprechen. Ein
  aktives `remove` mit `migrate` verlangt `migration_target` mit Ziel-
  Organization, Team-Unit, Team und Role-Slot. Der Hub validiert alle vier
  Bindungen im selben Projekt und erzeugt neue Tasks mit `source_task_id`;
  ein Reparent kann dieselben Task-IDs nach Lease-Stopp erneut routen.
- `POST /api/organizations/<id>/patches/grants` stellt aus der vollständigen,
  unveränderten Preview einen maximal fünf Minuten gültigen One-shot Grant
  aus. Erforderlich sind `If-Match`, `Idempotency-Key`, `X-Patch-Digest`,
  `X-Policy-Digest`, `X-Limit-Digest` und als reine Parent-Autorität
  `X-Organization-Admin-Grant`. Der Hub lädt Topologie, Policy und Limits
  autoritativ erneut und bindet den neuen `topology_patch` Grant an Principal,
  Tenant, Projekt, Organisation, Patch-Digest, Policy-Hash, Limit-Hash und
  erwartete Revision. Ein generischer `organization_admin`- oder `*`-Grant
  ist am Apply-Endpunkt ungültig.
- `POST /api/organizations/<id>/patches/apply` akzeptiert ausschließlich die
  vollständige, unveränderte Preview-Antwort und den dafür ausgestellten
  `X-Topology-Patch-Grant`. Erforderlich sind außerdem `If-Match`,
  `Idempotency-Key`, `X-Patch-Digest`, `X-Policy-Digest` und
  `X-Limit-Digest`. Der Hub löst Limits, Lifecycle,
  Capabilities, Kapazität und Separation of Duties unmittelbar vor der
  atomaren Unit-of-Work erneut auf und konsumiert/widerruft den Grant in
  derselben Transaktion. Nur ein Replay mit identischem Grant,
  Idempotency-Key, Request-Digest und bereits angewandtem Ergebnis ist danach
  zulässig. `drain` pausiert betroffene nichtterminale
  Tasks, beendet aktuelle Jobs/Leases und löst offene Gates/Handoffs
  strukturiert auf; `archive` bleibt bei aktiver Arbeit blockiert.

### Organization Bundle v2

- `GET /api/organization-bundles/export?organization_id=<id>` nutzt die
  gescopte Instanz ausschließlich zur Auswahl des exakten Definitionsgraphen.
  Die Antwort selbst ist projekt-/tenantübergreifend portabel und enthält
  weder Quellscope/Instanz-IDs noch lokale IDs oder Compiled Plans. Ohne Opt-in
  werden Runtime-Instanzen und Assignments ausgelassen. `include_instances=true`
  ergänzt ausschließlich ein zielseitig neu zu kompilierendes Rezept;
  `include_assignments=true` verlangt dieses Rezept und ergänzt nur
  pseudonymisierte Assignment-Intents ohne lokale Agent-URLs.
- `POST /api/organization-bundles/import-preview` nimmt
  `{bundle, conflict_strategy, project_id?, migrate_v1?,
  assignment_rebindings?, instance_admission_exception_refs?}` entgegen.
  `conflict_strategy` ist `fail`, `skip` oder `overwrite`. Ein v1-Team-Bundle
  wird nur mit `migrate_v1=true` deterministisch als Team-Slice migriert; der
  Hub erfindet dabei keine Organisationstopologie. Quellgebundene Legacy-Felder
  in `organization_instances`/`assignments` blockieren den Preview-Plan. Die
  Preview meldet `instance_import_mode=optional_target_recompile`.
  `target_rebind_contract` nennt – sofern ein exportierter Root-Blueprint im
  Bundle vorhanden ist – dessen `key@version`, den authentifizierten Zielscope,
  die Compile-/Instantiate-Endpunkte, Hub-seitige ID-Vergabe und das
  explizite lokale Assignment-Binding. Instanzrezepte werden ausschließlich im
  authentifizierten Zielscope neu kompiliert; Custom-Kompositionen benötigen
  eine passende, noch nicht verbrauchte Admission Exception. Team-only Bundles
  melden `available=false` und erfinden keinen Organization-Root.
- `POST /api/organization-bundles/import-grants` nimmt die vollständige
  Preview-Hülle entgegen und verlangt `If-Match`, `Idempotency-Key` sowie
  `X-Import-Plan-Digest`. Der Hub berechnet Preview, Zielrevision, Limits,
  Instanzpläne und Rebindings erneut. Nur ein exakter, an Principal und
  Zielscope gebundener Vergleich erzeugt einen kurzlebigen One-shot-Grant.
- `POST /api/organization-bundles/import-apply` nimmt die unveränderte
  Preview-Hülle entgegen und verlangt `X-Import-Plan-Digest`,
  `Idempotency-Key` und `X-Organization-Admin-Grant`. Zielrevision, Scope,
  Bundle-Digest und effektives Limitprofil werden in derselben Transaktion
  erneut geprüft. Definitionen, optionale neu kompilierte Instanzen, Teams,
  Slots, Relationen, target-lokal gebundene Assignments und Audit-Outbox werden
  atomar geschrieben; Teilimporte werden zurückgerollt. Eligibility,
  Kapazität und Separation of Duties werden direkt vor dem Assignment-Write
  erneut geprüft.

### Zweistufige Planung und Worker-Vorschläge

- `POST /api/organizations/<id>/goals` persistiert mit `Idempotency-Key` ein
  passives Root-Ziel im autoritativen Organization-/Tenant-/Project-Scope. Der
  geschlossene Body akzeptiert nur `goal`, `summary`, `constraints` und
  `acceptance_criteria`; Scope, Ersteller, Typ und Status werden ausschließlich
  serverseitig gesetzt. Der Aufruf startet weder Planung noch Worker-Dispatch
  und schreibt keine Queue.
- `POST /api/organizations/<id>/source-catalogs` akzeptiert ausschließlich
  `connection_id`, `queries` und `limit`. Der Hub fragt damit einen aktiven,
  zugelassenen Knowledge Index im aktuellen Organization-Scope ab, validiert
  Revision, Admission, Index-Run und Manifest erneut und weist die
  `SRC_*`-/`RUN_*`-Referenzen selbst zu. Persistiert wird ein inhaltsfreier,
  idempotenter Katalog; vom Aufrufer gelieferte Source-IDs sind keine
  Autorität.
- `GET /api/organizations/<id>/planning` liefert die paginierte Lineage
  Goal → Category-Todo-Revision → Planning-Track-Revision → Milestone → Task
  sowie passive Worker-Task-Proposals.
- `GET /api/organizations/<id>/goals/<goal-id>/planning/category-research/readiness`
  verlangt die Query-Parameter `unit_id`, `team_id`, `role_slot_id` und
  `catalog_task_id`. Die Projektion prüft den aktuellen Lifecycle, Scope,
  Membership, Topologie, eine konkrete eligible Assignment-/Agent-Bindung,
  Capability, Kapazität und den exakten Source Catalog, ohne dabei Tasks oder
  Queue-Einträge zu erzeugen.
- `POST /api/organizations/<id>/planning/<revision-id>/promote|adopt` verlangt
  `{expected_revision, expected_digest}` und führt ausschließlich die exakte,
  Hub-autorisierte CAS-Transition aus. Promotion erzeugt keine Tracks;
  Adoption materialisiert keine Tasks.
- `POST /api/organizations/<id>/goals/<goal-id>/planning/category-research`
  erzeugt mit `Idempotency-Key` genau eine Hub-Task `planning_research`. Der
  geschlossene `source_catalog_binding` identifiziert nur einen bereits
  persistierten, principal-/tenant-/scopegebundenen Katalog; Browserdaten
  dürfen keine `SRC_*`-/`RUN_*`-IDs autorisieren. Die Task erzeugt noch keine
  Category-Revision, Tracks oder ausführbaren Track-Tasks. Der Hub persistiert
  dabei eine konkrete aktive Role-Assignment-/Agent-Bindung mit `planning`,
  `research` und `source_analysis`; es gibt keinen globalen Worker-Fallback.
  Vor dem Forward werden Lifecycle, Topologie, Registrierung, Kapazität und
  WorkerJob-Lease erneut geprüft. Der Ziel-Worker akzeptiert nur den exakten,
  kurzlebig und payloadgebunden mit dem privaten Hub-Ed25519-Key signierten
  Transport an seinem service-only Intake. Der Worker besitzt nur den
  öffentlichen Verifikations-Keyring; sein Service-Token authentifiziert den
  HTTP-Transport, verleiht aber keine Hub-Autorität. Ohne kanonische
  `AGENT_URL` oder Verifikations-Keyring lehnt er geschlossen ab und routet
  selbst keine weitere Arbeit.
- `POST /api/worker-results/tasks/<task-id>/assignments/<assignment-id>/planning/category`
  nimmt die geschlossene Category-Ausgabe ausschließlich mit der exakten
  Worker-Result-Capability, Assignment-ID, Dispatch-Lease, Payload-Digest und
  `Idempotency-Key` an. Der Hub validiert `todos/todo.schema.json`, Grounding,
  DAG und Quality Profile und persistiert eine neue, zunächst unpromotete
  Revision.
- `POST /api/organizations/<id>/planning/<category-revision-id>/track-planning`
  erzeugt nach Promotion genau eine deterministische Hub-Task
  `planning_track_task` für die exakte Category-Revision. Der geschlossene Body
  enthält `expected_revision`, `expected_digest`, `expected_policy_hash`, die
  persistierte `unit_id`-/`team_id`-/`role_slot_id`-Bindung und die vollständige,
  kanonisch sortierte Menge aller nicht deferierten
  `source_category_item_ids`; `If-Match` und `Idempotency-Key` sind Pflicht.
  Der Worker erhält Category-Payload, Source-Katalog/Allowlist und Prompt-Hash,
  jedoch keine Routing-, Tool-, Kontext- oder Budgetautorität.
- `POST /api/worker-results/tasks/<task-id>/assignments/<assignment-id>/planning/tracks`
  akzeptiert ausschließlich die exakte, nicht abgelaufene
  Worker-Result-Capability und den aktuellen `WorkerJob`/Dispatch-Lease. Die
  geschlossene `organization_track_planning_result.v1`-Hülle enthält
  `category_revision_id`, die gebundenen `source_category_item_ids`, ein bis
  hundert `{artifact_id,payload}`-Kandidaten, strukturierte `exclusions` und
  einen `sha256:`-Digest über alle übrigen Hüllenfelder (sortierte Keys,
  kompakte Separatoren, ASCII-Escaping, keine nichtendlichen Zahlen, danach
  UTF-8). Der Hub
  validiert jeden Payload gegen `todos/todo.track.schema.json`, Quality Gates,
  vollständige Lineage, Scope, Authority Ceiling und den Cross-Track-DAG. Die
  Annahme persistiert nur passive Track-Revisionen; Antwortfelder
  `materialized_task_ids=[]`, `task_created=false` und `queue_write=false`
  machen die fehlende Ausführungswirkung explizit.
- `POST /api/organizations/<id>/planning/<category-revision-id>/derive-tracks`
  bleibt als expliziter Hub-Admin-Einstieg kompatibel und partitioniert
  ausschließlich eine exakt promotete Category-Revision. Es verlangt
  `If-Match`, Revision, Digest, Policy-Hash und `Idempotency-Key`;
  Überlappungen, Lücken oder verlorene/invertierte Dependencies werden
  abgewiesen. Die `reference-workflows/<key>/preview|derive`-Varianten lösen
  denselben Hub-validierten Track-Vertrag aus dem versionierten
  Workflow-Katalog auf; weder direkte noch Worker-gebundene Ableitung
  materialisiert Tasks.
- `POST /api/organizations/<id>/planning/<track-revision-id>/materialize`
  verbraucht einen separaten digestgebundenen Approval-Grant atomar mit den
  stabilen Plan-/Goal-/Task-Mappings. Ohne Grant wird nur eine passive
  Approval-Anfrage (`202`) erzeugt.
- `POST /api/organizations/<id>/planning/<track-revision-id>/tasks/<plan-task-id>/dispatch-next`
  materialisiert niemals implizit. Der Hub prüft Mapping, Dependencies,
  Team/Role-Slot, Capability, Kapazität, Risiko und Separation of Duties,
  persistiert die finale Assignment-/Agent-Entscheidung und erzeugt Task-CAS,
  Dispatch-Intent und Lease in einer Transaktion. `requested_worker_id` ist
  nur ein nicht autorisierender Hint.
- `POST /api/organizations/<id>/planning/dispatches/<intent-id>/retry` und
  `POST /api/organizations/<id>/planning/dispatches/pump` liefern den
  persistenten Hub→Worker-Outbox nach Backoff beziehungsweise nach abgelaufener
  Processing-Lease erneut aus. Ein vorhandener autoritativer WorkerJob wird
  als Crash-Replay-Receipt übernommen; der generische Delegationspfad darf
  eine persistierte Organization-Routingentscheidung nicht überschreiben.
- `POST /api/organizations/<id>/proposals/<proposal-id>/approve|reject`
  entscheidet eine exakte Proposal-Revision. Auch ein akzeptierter Vorschlag
  wird zunächst nur als revisionsgebundenes Track-Amendment eingeordnet.
- `POST /api/worker-results/tasks/<task-id>/assignments/<assignment-id>/proposals`
  ist ein Capability-only Result-Kanal. Er akzeptiert weder User-/Admin-Bearer
  noch unbekannte Felder und antwortet `202` mit `task_created=false` und
  `queue_write=false`. Nur der Hub darf später Rolle, Team, Agent und eine
  mögliche Taskmaterialisierung bestimmen.

Typische Fehler sind `400` fuer ungueltige Request-/Cursor-Vertraege, `403`
fuer fehlende oder falsch gebundene Grants, `404` fuer ausserhalb der eigenen
Membership liegende Ressourcen, `409` fuer Idempotency-Konflikte, `412` fuer
veraltete Revisionen, `422` fuer Compile-/Limit-/Lifecycle-Blocker und `428`
fuer fehlendes `If-Match`.

---

## Voice API Contract (Hub)

Alle Voice-Endpunkte sind authentifiziert (Bearer Token).

### `GET /v1/voice/capabilities`
- **Response 200:**
  ```json
  {
    "available": true,
    "provider": "voice-runtime",
    "models": [],
    "capabilities": ["audio_input", "transcription", "voice_command", "multimodal_audio_prompt"],
    "limits": {"max_audio_mb": 25},
    "privacy": {
      "store_audio_requested": false,
      "store_audio_effective": false,
      "raw_audio_persisted": false,
      "raw_audio_persisted_after_request": false,
      "transient_request_spooling": true
    },
    "health": {"ok": true, "status": "ok"}
  }
  ```

### `POST /v1/voice/transcribe`
- **Body:** `multipart/form-data` mit `file` plus optional `language`, `model`, `mode`
- **Limits:** `VOICE_MAX_AUDIO_MB`, `VOICE_TIMEOUT_SEC`; Live-Streams nutzen
  zusätzlich `VOICE_STREAM_TIMEOUT_SEC` als Gesamtlimit für Capture und
  Finalisierung. Der Runtime-Wert muss mindestens der maximalen Hub-Live-
  Stream-Frist entsprechen (standardmäßig jeweils 300 Sekunden), da die
  Runtime längere angeforderte Fristen auf dieses Limit kürzt.
- **Response 200:**
  ```json
  {
    "text": "string",
    "language": "string|null",
    "duration_ms": "number|null",
    "model": "string",
    "provider": "string",
    "warnings": []
  }
  ```

### `POST /v1/voice/command`
- **Body:** `multipart/form-data` mit `file` plus optional `command_context`
- **Response 200:**
  ```json
  {
    "transcript": "string",
    "intent": "string|null",
    "confidence": "number|null",
    "proposed_goal": "string|null",
    "requires_approval": true,
    "audit_id": "string"
  }
  ```

### `POST /v1/voice/goal`
- **Body:** `multipart/form-data` mit `file` plus optional `create_tasks`, `governance_mode`, `approved`
- **Response 200:**
  ```json
  {
    "goal_id": "string|null",
    "transcript": "string",
    "created_tasks": false,
    "requires_review": true
  }
  ```

### Langzeit-Live-Transkription (bis zu 8 Stunden)

Der Hub besitzt einen dauerhaften Parent-Task und legt für jedes abgeschlossene
Segment einen Child-Task vom Typ `voice_transcription` an. Clients halten die
Audiofreigabe offen und senden rollierende WAV-/PCM-Segmente. Roh-Audio wird
weder im Run-Ledger noch in Tasks oder Result-Artefakten gespeichert. Der
HTTP-Parser darf große Multipart-Bodies während genau dieses Requests temporär
spulen; nach Request-Ende besteht keine Ananta-Roh-Audio-Retention. Das wird in
den Capabilities durch `transient_request_spooling=true` und
`raw_audio_persisted_after_request=false` explizit ausgewiesen.

#### `POST /v1/voice/live-runs/lease`

- **Body:** `{"profile_id":"default"}`; ein `Idempotency-Key` ist nicht nötig.
- **Response 200 (`data`):**
  ```json
  {
    "lease_token": "hub-signed-token",
    "expires_at": 1760000600.0,
    "profile_id": "default"
  }
  ```
- Der Hub-signierte Start-Lease gilt zehn Minuten und ist pseudonym an
  Principal, Profil und die aktuelle Generation des Voice-Deletion-Tombstones
  gebunden. Er ist innerhalb dieser Frist wiederverwendbar, enthält aber keine
  Aufnahmeberechtigung für ein anderes Profil oder einen anderen Principal.
- Jede Profil-Löschung ändert die Generation und macht bereits ausgegebene
  Leases ungültig. Ein nach abgeschlossener Löschung neu ausgegebener Lease
  erlaubt eine explizite Neuanlage für dasselbe Profil.

#### `POST /v1/voice/live-runs`

- **Header:** `Idempotency-Key` ist erforderlich.
- **Body:**
  ```json
  {
    "lease_token": "hub-signed-token",
    "source": "microphone|system_audio",
    "profile_id": "default",
    "configuration_session_id": null,
    "language": "de",
    "segment_duration_seconds": 60,
    "max_duration_seconds": 28800,
    "overlap_milliseconds": 1000
  }
  ```
- `segment_duration_seconds`: 60 bis 120.
- `max_duration_seconds`: 60 bis 28.800 (acht Stunden).
- `max_duration_seconds` muss mindestens `segment_duration_seconds` entsprechen.
- `overlap_milliseconds`: 0 bis 5.000 und kleiner als die Segmentdauer.
- `lease_token` ist erforderlich. Manipulierte, abgelaufene, profilfremde oder
  durch eine zwischenzeitliche Löschung veraltete Leases werden abgewiesen.
  Der Hub prüft die gebundene Tombstone-Generation erneut nach Run-Persistenz,
  nach Parent-Task-Anlage und unmittelbar vor der Antwort; bei einem Race
  werden exakt der neue Run und sein Task-Baum kompensierend entfernt.
- Eine Wiederholung mit demselben Schlüssel und demselben Body liefert
  `idempotent_replay=true`; eine abweichende Konfiguration liefert `409`.

#### `PUT /v1/voice/live-runs/<run_id>/segments/<sequence>`

- **Header:** stabiler `Idempotency-Key` pro Run und Sequenz. Bei einem Retry
  müssen exakt dieselben Audiodaten und Metadaten verwendet werden.
- **Multipart-Body:** `file` als vollständiges PCM-WAV (mono, 16 Bit) oder
  raw PCM16/16 kHz/mono sowie relative `started_at_ms`, `ended_at_ms`,
  `duration_ms` und `overlap_milliseconds`.
- **Alternative JSON-Registrierung:**
  ```json
  {
    "result_ref": "voice-result-...",
    "started_at_ms": 60000,
    "ended_at_ms": 120000,
    "duration_ms": 60000,
    "overlap_milliseconds": 1000
  }
  ```
- Der Hub bindet Audio mit einem tenant-, operation- und
  idempotency-key-spezifischen HMAC. Das ist kein wiederverwendbarer
  Audio-Fingerabdruck. Gleiche Sequenz mit anderem Schlüssel, Audio oder
  Metadaten liefert `409`.
- Statuswerte: `processing`, `completed`, `failed`; fehlende Sequenzen werden
  im Snapshot als `gap` dargestellt. `attempt_count` und `failure_code`
  unterstützen kontrollierte Retries.
- Die Upload-Antwort enthält den aktualisierten Datensatz unter `segment` und
  das sofort sichtbare ASR-Ergebnis unter `result`. Bei angeforderter Korrektur
  trägt das Segment zunächst `text_state=provisional`, `text_revision=1` und
  `correction_status=queued|processing`. Die ASR-Bestätigung bleibt dabei
  `status=completed`, damit Upload-Retry und lokaler Spool-Cursor kompatibel
  bleiben.
- Der Hub legt die optionale Korrektur als separaten Child-Task an. Ihr
  Abschluss veröffentlicht für dieselbe `sequence` eine höhere
  `timeline_revision` und `text_revision=2`; `text_state=final` bezeichnet
  angewendete Korrektur, `final_uncorrected` einen bewusst unveränderten oder
  fehlgeschlagenen Fallback. Der Klartext bleibt in verschlüsselten
  Ergebnisartefakten, nicht im Segment-Ledger oder Task-Kontext.
- Aus Lastgründen wird in der Upload-Antwort kein Gesamttext neu
  zusammengesetzt (`composed_transcript=null`).

#### `POST /v1/voice/live-runs/<run_id>/heartbeat`

```json
{
  "last_local_sequence": 42,
  "gaps": [17]
}
```

Der Heartbeat aktualisiert nur das persistente Resume-/Gap-Ledger und
entschlüsselt keine früheren Ergebnisartefakte. Die Hub-Frist wird dadurch
nicht verlängert.

#### `GET /v1/voice/live-runs/<run_id>`

- Query: `after_sequence` (Default `-1`), optional `after_revision`, `limit`
  (maximal 600), `include_text=true|false`.
- Liefert `run`, `segments`, `gaps`, `resume`, `page` und bei
  `include_text=true` den autorisiert aus verschlüsselten Segmentartefakten
  zusammengesetzten `composed_transcript`.
- Ohne `after_revision` bleibt der sequenzbasierte Voll-/Resume-Snapshot
  abwärtskompatibel. Mit `after_revision=N` liefert der Hub nur Segmente mit
  einer neueren globalen `timeline_revision`, sortiert nach Revision. Der
  nächste Cursor steht in `page.next_after_revision`; `page.has_more=true`
  verlangt die nächste Seite mit genau diesem Cursor. Delta-Antworten tragen
  absichtlich `composed_transcript=null`, damit nur die betroffenen
  verschlüsselten Segmentartefakte entschlüsselt werden.
- Clients führen die sichtbare Timeline nach `sequence` zusammen und ersetzen
  Text ausschließlich durch eine höhere `text_revision`. Eine verspätete
  Revision 1 darf dadurch niemals eine bereits sichtbare Revision 2
  zurückrollen.
- `resume.acknowledged_through_sequence` ist die höchste lückenlos
  abgeschlossene Sequenz; `resume.next_sequence` ist der erste erneut zu
  sendende Audio-Cursor. `resume.pending_correction_sequences` ist unabhängig
  davon und enthält noch ausstehende Textrevisionen.

#### `POST /v1/voice/live-runs/<run_id>/stop`

```json
{"last_sequence": 479, "reason": "user_stop"}
```

Stop ist idempotent und wartet nicht stillschweigend über laufende Segmente
oder Korrekturen hinweg: frische Arbeit liefert
`voice_live_run.segments_in_flight` als retriable `409`. Eine nach zehn Minuten
abgelaufene ASR-Processing-Lease wird als Gap finalisiert; eine nach fünf
Minuten abgelaufene Korrektur-Lease wird als `final_uncorrected` übernommen.
Der Angular-Client pollt Revisionen während dieses begrenzten Stop-Drains
weiter. Das verschlüsselte Endartefakt ist ein begrenztes Manifest aus
Segment-Refs und Lücken, kein monolithischer Acht-Stunden-Klartext. Der
vollständige Text bleibt über den autorisierten GET-Snapshot abrufbar.

Die Capture-Timeline endet spätestens nach `max_duration_seconds`. Innerhalb
einer einstündigen Drain-/Finalisierungsfrist dürfen bereits innerhalb dieser
Timeline erfasste Offline-Segmente nachgereicht werden; danach wechselt der Run
durabel auf `expired`. Run- und Segment-Ledger, Parent-/Child-Tasks,
Idempotency-Daten und verschlüsselte Ergebnisartefakte werden durch
`DELETE /v1/voice/privacy/<profile_id>` gemeinsam gelöscht.

### Fehlerformat (konsistent)

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "retriable": false
  }
}
```

Typische Codes: `validation.missing_file`, `validation.file_too_large`, `voice.timeout`, `voice.runtime_unavailable`, `policy_denied`.

Voice-Exposition folgt `exposure_policy.voice`. In Standardprofilen gilt:
- Voice-Endpunkte sind konfigurierbar aktiv/deaktivierbar.
- Goal-Erzeugung aus Audio kann explizite Freigabe verlangen (`approved=true`).
- Roh-Audio-Persistenz bleibt fail-closed; `GET /v1/voice/capabilities` liefert dafuer ein `privacy`-Objekt.

---

## System Endpunkte

### Health Check
- **URL:** `/health`
- **Methode:** `GET`
- **Beschreibung:** Prüft den Status des Agenten.
- **Auth erforderlich:** Nein
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "agent": "name",
      "checks": {
        "shell": {"status": "ok"},
        "llm_providers": {"ollama": "ok"}
      }
    }
  }
  ```

### Readiness Check
- **URL:** `/ready`
- **Methode:** `GET`
- **Beschreibung:** Prüft, ob der Agent und seine Abhängigkeiten (Hub, LLM) bereit sind.
- **Auth erforderlich:** Nein
- **Rückgabe:** Detaillierter Status der Subsysteme im `data` Feld.
  ```json
  {
    "status": "success",
    "data": {
      "ready": true,
      "checks": {
        "hub": {"status": "ok", "latency": 0.05},
        "llm": {"provider": "ollama", "status": "ok", "latency": 0.1}
      }
    }
  }
  ```

### Metriken
- **URL:** `/metrics`
- **Methode:** `GET`
- **Beschreibung:** Liefert Prometheus-Metriken.
- **Auth erforderlich:** Nein
- **Rückgabe:** Plain text (Prometheus Format)

### Agent Registrierung
- **URL:** `/register`
- **Methode:** `POST`
- **Beschreibung:** Registriert einen neuen Agenten.
- **Auth erforderlich:** Nein (Rate-limited)
- **Body:**
  ```json
  {
    "name": "string",
    "url": "string",
    "role": "worker|hub",
    "token": "optional string"
  }
  ```
- **Rückgabe:** `{"status": "success", "data": {"status": "registered"}}`

### Agenten auflisten
- **URL:** `/agents`
- **Methode:** `GET`
- **Beschreibung:** Listet alle bekannten Agenten auf.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Token rotieren
- **URL:** `/rotate-token`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "rotated", "new_token": "..."}}`

---

## Authentifizierung & Benutzerverwaltung

### Login
- **URL:** `/login`
- **Methode:** `POST`
- **Beschreibung:** Meldet einen Benutzer an und liefert ein JWT.
- **Rate-Limited:** Ja (max 5 Versuche / Minute)
- **Body:**
  ```json
  {
    "username": "string",
    "password": "string"
  }
  ```
- **Rückgabe:** 
  ```json
  {
    "status": "success",
    "data": {
      "access_token": "...",
      "refresh_token": "...",
      "username": "...",
      "role": "..."
    }
  }
  ```

### Token erneuern
- **URL:** `/refresh-token`
- **Methode:** `POST`
- **Body:** `{"refresh_token": "string"}`
- **Rückgabe:** 
  ```json
  {
    "status": "success",
    "data": {
      "access_token": "...",
      "refresh_token": "...",
      "username": "...",
      "role": "..."
    }
  }
  ```

### Aktueller Benutzer (/me)
- **URL:** `/me`
- **Methode:** `GET`
- **Beschreibung:** Gibt Informationen über den aktuell angemeldeten Benutzer zurück.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** 
  ```json
  {
    "status": "success",
    "data": {
      "username": "...",
      "role": "...",
      "mfa_enabled": true
    }
  }
  ```

### Passwort ändern (Self-Service)
- **URL:** `/change-password`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (User)
- **Body:** `{"old_password": "...", "new_password": "..."}`
- **Rückgabe:** `{"status": "success", "data": {"status": "password_changed"}}`

### Benutzer auflisten (Admin)
- **URL:** `/users`
- **Methode:** `GET`
- **Auth erforderlich:** Ja (Admin)
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Benutzer anlegen (Admin)
- **URL:** `/users`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"username": "...", "password": "...", "role": "user|admin"}`
- **Rückgabe:** `{"status": "success", "data": {"status": "user_created", "user": {...}}}`

### Benutzer löschen (Admin)
- **URL:** `/users/<username>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja (Admin)
- **Rückgabe:** `{"status": "success", "data": {"status": "user_deleted"}}`

### Passwort-Reset (Admin)
- **URL:** `/users/<username>/reset-password`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"new_password": "..."}`
- **Rückgabe:** `{"status": "success", "data": {"status": "password_reset"}}`

### Benutzer-Rolle aktualisieren (Admin)
- **URL:** `/users/<username>/role`
- **Methode:** `PUT`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `{"role": "user|admin"}`
- **Rückgabe:** `{"status": "success", "data": {"status": "role_updated"}}`

---

## Multi-Faktor-Authentifizierung (MFA)

### MFA Setup initialisieren
- **URL:** `/mfa/setup`
- **Methode:** `POST`
- **Beschreibung:** Generiert ein neues MFA-Secret und einen QR-Code für den aktuellen Benutzer.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"status": "success", "data": {"secret": "...", "qr_code": "base64..."}}`

### MFA Verifizieren & Aktivieren
- **URL:** `/mfa/verify`
- **Methode:** `POST`
- **Beschreibung:** Verifiziert den ersten TOTP-Token und aktiviert MFA für den Account. Liefert 10 Backup-Codes zurück.
- **Rate-Limited:** Ja
- **Auth erforderlich:** Ja (User)
- **Body:** `{"token": "123456"}`
- **Rückgabe:** `{"status": "success", "data": {"status": "mfa_enabled", "backup_codes": ["...", ...]}}`

### MFA Deaktivieren
- **URL:** `/mfa/disable`
- **Methode:** `POST`
- **Beschreibung:** Deaktiviert MFA für den aktuellen Benutzer.
- **Auth erforderlich:** Ja (User)
- **Rückgabe:** `{"status": "success", "data": {"status": "mfa_disabled"}}`

---

## Konfiguration & Role Templates

### Konfiguration abrufen
- **URL:** `/config`
- **Methode:** `GET`
- **Beschreibung:** Gibt die aktuelle Agenten-Konfiguration zurück.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Konfiguration aktualisieren
- **URL:** `/config`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** JSON Objekt mit Konfigurationswerten.
- **Rückgabe:** `{"status": "success", "data": {"status": "updated", "config": {...}}}`

### CLI Session Mode (stateful Execution-Backends)
- **URL:** `/config`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Beschreibung:** Aktiviert optionale stateful Multi-Turn-Sessions fuer CLI-Execution-Backends (z. B. OpenCode/Codex).
- **Relevante Keys:**
  ```json
  {
    "cli_session_mode": {
      "enabled": true,
      "stateful_backends": ["opencode", "codex"],
      "max_turns_per_session": 40,
      "max_sessions": 200,
      "allow_task_scoped_auto_session": true
    }
  }
  ```

### Exposure-Policy (OpenAI-Compat / MCP)
- **URL:** `/config`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Beschreibung:** Steuert explizit, welche Expositionspfade aktiv sind und welche Auth-Quellen erlaubt sind.
- **Relevante Keys:**
  ```json
  {
    "exposure_policy": {
      "openai_compat": {
        "enabled": true,
        "allow_agent_auth": true,
        "allow_user_auth": true,
        "require_admin_for_user_auth": true,
        "allow_files_api": true,
        "instance_id": "hub-main",
        "max_hops": 3
      },
      "mcp": {
        "enabled": false,
        "allow_agent_auth": false,
        "allow_user_auth": false,
        "require_admin_for_user_auth": true,
        "emit_audit_events": true
      }
    }
  }
  ```

### SGPT Stateful Sessions

#### Session erstellen
- **URL:** `/api/sgpt/sessions`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body (Beispiel):**
  ```json
  {
    "backend": "opencode",
    "model": "opencode/glm-5-free",
    "conversation_id": "conv-123"
  }
  ```

#### Session-Turn ausfuehren
- **URL:** `/api/sgpt/sessions/{session_id}/turn`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body (Beispiel):**
  ```json
  {
    "prompt": "Bitte fuehre den naechsten Schritt aus"
  }
  ```
- **Rückgabe:** Additiv mit `session_id`, `session_turn`, `routing.session_mode=stateful`.

#### Session lesen/listen/schliessen
- `GET /api/sgpt/sessions`
- `GET /api/sgpt/sessions/{session_id}`
- `DELETE /api/sgpt/sessions/{session_id}`

### OpenAI-Compat Conversation-Metadaten (additiv)
- `POST /v1/chat/completions` und `POST /v1/responses` akzeptieren optional `metadata.conversation_id`/`session_id` (oder Top-Level).
- Antworten enthalten bei gesetzten Metadaten additiv ein `conversation`-Objekt:
  - `conversation_id`
  - `session_id`
  - `turn_id`
  - `mode`

---

## OpenAI-kompatible Exposition

### Modelle auflisten
- **URL:** `/v1/models`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Hinweis:** Zugriff unterliegt `exposure_policy.openai_compat`.

### Chat Completions
- **URL:** `/v1/chat/completions`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Hinweis:** Zugriff unterliegt `exposure_policy.openai_compat`.
- **Hop-/Loop-Guard:** Header `X-Ananta-Instance-ID` und `X-Ananta-Hop-Count` werden serverseitig geprueft.

### Responses API
- **URL:** `/v1/responses`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Hinweis:** Zugriff unterliegt `exposure_policy.openai_compat`.
- **Hop-/Loop-Guard:** Header `X-Ananta-Instance-ID` und `X-Ananta-Hop-Count` werden serverseitig geprueft.

### Remote-Ananta Provider (additiv)
- **URL:** `/config` (`POST`)
- **Beschreibung:** `remote_ananta_backends` erlaubt explizite OpenAI-kompatible Remote-Hub-Ziele als Provider-Typ.
- **Beispiel:**
  ```json
  {
    "remote_ananta_backends": [
      {
        "id": "ananta_remote_prod",
        "name": "Ananta Remote Prod",
        "base_url": "https://ananta-remote.example/v1/chat/completions",
        "models": ["gpt-4o"],
        "instance_id": "remote-prod-1",
        "max_hops": 5
      }
    ]
  }
  ```

### Dateien (OpenAI-kompatibel)
- **URLs:** `/v1/files`, `/v1/files/<file_id>`
- **Methoden:** `GET`, `POST`
- **Auth erforderlich:** Ja
- **Hinweis:** zusaetzlich durch `exposure_policy.openai_compat.allow_files_api` gesteuert.

### Ananta Capabilities fuer OpenAI-Compat
- **URL:** `/v1/ananta/capabilities`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Beschreibung:** Liefert aktive OpenAI-Compat-Feature-Flags und effektive Exposure-Policy fuer Operator/Client-Diagnostik.
- **Hinweis:** Additiv wird `adapter_registry` mit Registry-Metadaten fuer den Expositions-Adapter ausgegeben.

---

## MCP-Exposition (additiv)

### MCP Capabilities
- **URL:** `/v1/mcp/capabilities`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Hinweis:** Zugriff unterliegt `exposure_policy.mcp`.
- **Beschreibung:** Liefert MCP-Feature-Flags, effektive Policy und Anzahl registrierter Tools/Resources.
- **Hinweis:** Additiv wird `adapter_registry` mit Registry-Metadaten fuer den Expositions-Adapter ausgegeben.

### MCP JSON-RPC Endpoint
- **URL:** `/v1/mcp`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Hinweis:** Zugriff unterliegt `exposure_policy.mcp`.
- **Transport:** JSON-RPC 2.0 (Response enthaelt additiv `trace_id` fuer Operator-Diagnostik).

Unterstuetzte Methoden (erste Ausbaustufe):
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`

Erste freigegebene Tools/Resources:
- Tools: `health.get`, `providers.list_models`, `tasks.list`, `tasks.get`, `artifacts.list`, `knowledge.list_collections`, `evolution.providers.list`, `evolution.analyze`, `evolution.proposals.list`
- Resources: `ananta://system/health`, `ananta://providers/models`, `ananta://tasks/recent`, `ananta://artifacts/list`, `ananta://knowledge/collections`, `ananta://evolution/providers`

### Role Templates auflisten (API: `/templates`)
- **URL:** `/templates`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Role Template erstellen (API: `/templates`)
- **URL:** `/templates`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** Template Objekt.
- **Rückgabe:** `{"status": "success", "data": {...}}` (Erstelltes Template mit ID)
- **Fehlerfaelle:**
  - `400 template_name_required`
  - `400 unknown_template_variables` (strict + unbekannte Variablen)
  - `400 context_unavailable_template_variables` (strict + bekannte aber im Kontext ungueltige Variablen)
  - `400 template_validation_failed` (strict + gemischte Fehlerbilder)
  - `409 template_name_exists`

### Role Template aktualisieren (API: `/templates/<tpl_id>`)
- **URL:** `/templates/<tpl_id>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Body:** Template Felder.
- **Rückgabe:** `{"status": "success", "data": {...}}`
- **Fehlerfaelle:** wie beim Erstellen; bei Validation liefert die API differenzierte Felder (`unknown_variables`, `context_invalid_variables`, `deprecated_variables`).

### Role Template loeschen (API: `/templates/<tpl_id>`)
- **URL:** `/templates/<tpl_id>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "deleted"}}`

### Template-Variablen-Registry lesen
- **URL:** `/templates/variable-registry`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status":"success","data":{"version":2,"variables":[...],"by_scope":{...},"allowed_names":[...]}}`
- **Beschreibung:** Liefert die kanonische Variable-Registry inkl. Scope-, Alias- und Stabilitätsmetadaten.

### Template-Beispielkontexte lesen
- **URL:** `/templates/sample-contexts`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status":"success","data":{"default_context_scope":"task","contexts":{...}}}`
- **Beschreibung:** Liefert repräsentative Beispiel-Kontexte fuer task/team/role/blueprint/agent/artifact/domain_specific.

### Template validieren
- **URL:** `/templates/validate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** `{"prompt_template":"...","context_scope":"task|team|..."}`
- **Rückgabe:** Validierungsbericht mit `unknown_variables`, `context_invalid_variables`, `deprecated_variables`, `issues`, `diagnostics`.

### Template-Vorschau rendern
- **URL:** `/templates/preview`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** `{"prompt_template":"...","context_scope":"task","sample_context":"task","context_payload":{...}}`
- **Rückgabe:** `preview.rendered_text`, `missing_variables`, verwendeter Beispielkontext und zugehörige Validierungsdaten.

### Template-Validierungsdiagnostik (Operator)
- **URL:** `/templates/validation-diagnostics`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** wie bei `/templates/preview`
- **Rückgabe:** sichere Diagnostik mit Severity/Issue-Codes, Rendering-Vollständigkeit und Kontext-Keys (ohne Kontext-Werte).

### Template-Runtime-Contract lesen
- **URL:** `/templates/runtime-contract`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status":"success","data":{"version":1,"renderer":{...},"context_fields":[...]}}`
- **Beschreibung:** Dokumentiert den Runtime-Rendervertrag (Kontextfelder, Validierungsmodus, Ersetzungssemantik).

---

## Task Management

### Global Terminal Logs
- **URL:** `/logs`
- **Methode:** `GET`
- **Beschreibung:** Liefert die letzten 100 Einträge des globalen Terminal-Logs.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Schritt vorschlagen
- **URL:** `/step/propose`
- **Methode:** `POST`
- **Body:** `TaskStepProposeRequest`
- **Beschreibung:** Nutzt das LLM, um den nächsten Schritt basierend auf einem Prompt vorzuschlagen.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}` (TaskStepProposeResponse)

### Schritt ausführen
- **URL:** `/step/execute`
- **Methode:** `POST`
- **Body:** `TaskStepExecuteRequest`
- **Beschreibung:** Führt ein Shell-Kommando aus.
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}` (TaskStepExecuteResponse)

### Tasks auflisten
- **URL:** `/tasks`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Task erstellen
- **URL:** `/tasks`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"id": "...", "status": "created"}}`

### Task Details
- **URL:** `/tasks/<tid>`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Task aktualisieren
- **URL:** `/tasks/<tid>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"id": "...", "status": "updated"}}`

### Task zuweisen
- **URL:** `/tasks/<tid>/assign`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "assigned", "agent_url": "..."}}`

### Task Schritt vorschlagen
- **URL:** `/tasks/<tid>/step/propose`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Task Schritt ausführen
- **URL:** `/tasks/<tid>/step/execute`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Task Logs
- **URL:** `/tasks/<tid>/logs`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Task Logs Stream
- **URL:** `/tasks/<tid>/stream-logs`
- **Methode:** `GET`
- **Beschreibung:** Liefert Logs als Server-Sent Events (SSE).
- **Auth erforderlich:** Ja

---

## System Statistiken & Events

### System Status (Echtzeit)
- **URL:** `/stats`
- **Methode:** `GET`
- **Beschreibung:** Liefert aktuelle Auslastung (CPU, RAM), Agenten-Status, Task-Zähler und Shell-Pool Status.
- **Auth erforderlich:** Ja
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "agents": {"total": 1, "online": 1, "offline": 0},
      "tasks": {"total": 5, "completed": 2, "failed": 0, "todo": 2, "in_progress": 1},
      "shell_pool": {"total": 5, "free": 4, "busy": 1},
      "resources": {"cpu_percent": 12.5, "ram_bytes": 102456789},
      "timestamp": 123456789.0,
      "agent_name": "hub-01"
    }
  }
  ```

### Statistik Historie
- **URL:** `/stats/history`
- **Methode:** `GET`
- **Beschreibung:** Liefert historische Snapshots der System-Statistiken.
- **Auth erforderlich:** Ja
- **Query Parameter:** `limit` (int), `offset` (int)
- **Rückgabe:** `{"status": "success", "data": [...]}`

### System Events (SSE)
- **URL:** `/events`
- **Methode:** `GET`
- **Beschreibung:** Streamt System-Events (z.B. Task-Updates, Agenten-Statusänderungen) als Server-Sent Events.
- **Auth erforderlich:** Ja

### Audit Logs (Admin)
- **URL:** `/audit-logs`
- **Methode:** `GET`
- **Beschreibung:** Ruft die Audit-Logs des Systems ab.
- **Auth erforderlich:** Ja (Admin)
- **Query Parameter:** `limit` (int), `offset` (int)
- **Rückgabe:** `{"status": "success", "data": [...]}`

---

## LLM

### CLI LLM ausfuehren (`/api/sgpt/execute`)
- **URL:** `/api/sgpt/execute`
- **Methode:** `POST`
- **Beschreibung:** Führt einen CLI-LLM-Befehl aus (SGPT, OpenCode, Aider oder Mistral Code).
- **Body:** `{"prompt": "...", "options": ["--shell", "--md", "--cache", "--no-cache", "--no-interaction"], "backend": "sgpt|opencode|aider|mistral_code|auto", "model": "optional-model-id"}`
- **Rückgabe:** `{"status": "success", "data": {"output": "...", "errors": "...", "backend": "sgpt|opencode|aider|mistral_code"}}`
- **Hinweis:** `options` werden backend-spezifisch validiert; nicht unterstützte Flags führen zu `400`.

### Verfügbare CLI Backends
- **URL:** `/api/sgpt/backends`
- **Methode:** `GET`
- **Beschreibung:** Liefert die unterstützten CLI-Backends, deren Capabilities und explizit nicht unterstützte Integrationen.
- **Rückgabe (Beispiel):**
  ```json
  {
    "status": "success",
    "data": {
      "configured_backend": "auto",
      "supported_backends": {
        "sgpt": {
          "display_name": "ShellGPT",
          "supports_model": true,
          "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"]
        },
        "opencode": {
          "display_name": "OpenCode",
          "supports_model": true,
          "supported_flags": []
        },
        "aider": {
          "display_name": "Aider",
          "supports_model": true,
          "supported_flags": []
        },
        "mistral_code": {
          "display_name": "Mistral Code",
          "supports_model": true,
          "supported_flags": []
        }
      }
    }
  }
  ```

### Text generieren (mit Tool-Calling und optionalem Streaming)
- **URL:** `/llm/generate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Tool-Ausführung erfordert Admin)
- **Beschreibung:** Führt eine LLM-Anfrage aus. Unterstützt optionales Tool-Calling (Allow-/Deny-List, Bestätigungspfad) und Streaming per Server-Sent Events (SSE).
- **Request Body:**
  ```json
  {
    "prompt": "string",                     // Freitext-Eingabe
    "tool_calls": [                          // Optional: vom Client vorgeschlagene Tool-Aufrufe
      {"name": "tool_name", "args": {}}
    ],
    "confirm_tool_calls": true,              // Optional: bestätigt, dass tool_calls direkt ausgeführt werden dürfen
    "confirmed": true,                       // Alias zu confirm_tool_calls
    "stream": false,                         // Falls true: Streaming-Antwort als SSE
    "history": [ {"role": "user|assistant|system", "content": "..."} ],
    "config": {
      "provider": "lmstudio|ollama|openai|anthropic",
      "model": "string|auto",
      "base_url": "string",
      "api_key": "string",
      "timeout": 30                         // Optional: per-Request Timeout in Sekunden; überschreibt globalen Default
    }
  }
  ```
  - Hinweis zu `config.timeout`: Gilt nur für diesen Request. Ohne Angabe wird der globale Wert `settings.http_timeout` verwendet (Standard 60 Sekunden).
- **Antworten (Non-Streaming):**
  - Erfolgreich ohne Tool-Calls:
    ```json
    {
      "status": "success",
      "data": {"response": "string"}
    }
    ```
  - JSON-Format mit vorgeschlagenen Tool-Calls (Benutzer ist kein Admin oder Bestätigung fehlt):
    ```json
    {
      "status": "success",
      "data": {
        "response": "Kurze Antwort",
        "requires_confirmation": true,
        "thought": "warum Aktion",
        "tool_calls": [ {"name": "tool", "args": {}} ]
      }
    }
    ```
  - Blockierte Tools (Deny-/Nicht erlaubt):
    ```json
    {
      "status": "success",
      "data": {
        "response": "Tool calls blocked: <liste>",
        "tool_results": [ {"tool":"name","success":false,"error":"tool_not_allowed"} ],
        "blocked_tools": ["name"]
      }
    }
    ```
  - Ausführung bestätigter Tool-Calls (Admin):
    ```json
    {
      "status": "success",
      "data": {
        "response": "string",
        "tool_results": [ {"tool":"name","success":true,"output":"..."} ]
      }
    }
    ```
- **Streaming:**
  - Bei `stream=true` wird als `text/event-stream` geantwortet. Nachrichten im Format `data: <chunk>\n\n`, abgeschlossen mit `event: done\ndata: [DONE]\n\n`.
  - Im Streaming-Modus erfolgt KEIN Tool-Calling; es wird Klartext gestreamt.

- **Status-/Fehlerfälle:**
  - `400 invalid_json | missing_prompt | llm_not_configured | llm_api_key_missing | llm_base_url_missing`
  - `403 forbidden` wenn Tool-Execution ohne Admin-Rechte angefragt wird.

### SGPT Hybrid-RAG Erweiterung

#### SGPT Execute mit Hybrid-Kontext
- **URL:** `/api/sgpt/execute`
- **Methode:** `POST`
- **Beschreibung:** Fuehrt SGPT/OpenCode/Aider/Mistral Code aus und kann optional Hybrid-RAG-Kontext einbetten.
- **Body:**
  ```json
  {
    "prompt": "Where is timeout handling implemented?",
    "options": ["--no-interaction"],
    "use_hybrid_context": true,
    "backend": "auto"
  }
  ```
- **Antwort (Beispiel):**
  ```json
  {
    "status": "success",
    "data": {
      "output": "....",
      "errors": "",
      "context": {
        "strategy": {"repository_map": 4, "semantic_search": 1, "agentic_search": 1},
        "policy_version": "v1",
        "chunk_count": 6,
        "token_estimate": 520
      }
    }
  }
  ```

#### SGPT Context Endpoint
- **URL:** `/api/sgpt/context`
- **Methode:** `POST`
- **Beschreibung:** Liefert den selektierten Kontextmix (Aider Symbol-Map, agentische Dateisuche, LlamaIndex Chunks).
- **Body:**
  ```json
  {
    "query": "find invoice timeout bug in module.py",
    "include_context_text": true
  }
  ```

#### SGPT Source Preview Endpoint
- **URL:** `/api/sgpt/source`
- **Methode:** `POST`
- **Beschreibung:** Gibt eine sichere Vorschau fuer eine zitierte Quelldatei zurueck.
- **Body:**
  ```json
  {
    "source_path": "agent/routes/sgpt.py",
    "max_chars": 1600
  }
  ```
- **Antwort (Beispiel):**
  ```json
  {
    "status": "success",
    "data": {
      "source_path": "agent/routes/sgpt.py",
      "preview": "def execute_sgpt():\n    ...",
      "truncated": true,
      "line_count": 42
    }
  }
  ```
## Team Management

Blueprint-first ist der bevorzugte Team-Workflow. Der produktnahe Standardmodus nutzt das Modell **Role Template -> Blueprint -> Team**; interne Lifecycle-Konzepte (`snapshot`, `drift`, `reconcile`) bleiben als Advanced-Sicht verfuegbar.

### Blueprint-Katalog (produktnahe Zusammenfassung)
- **URL:** `/teams/blueprints/catalog`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Beschreibung:** Liefert den offiziellen Standard-Blueprint-Katalog mit kompakten Kartenfeldern fuer den Standardmodus (intended use, when to use, expected outputs, safety/review stance) plus Public-Model-Terminologie.
- **Rueckgabe (Auszug):**
  ```json
  {
    "status": "success",
    "data": {
      "public_model": {
        "template_term": "Role Template",
        "template_api_term": "template",
        "blueprint_term": "Blueprint",
        "team_term": "Team",
        "default_entry_path": "Start with a blueprint, then instantiate a team.",
        "advanced_concepts": ["snapshot", "drift", "reconcile"]
      },
      "items": [
        {
          "id": "...",
          "name": "Scrum",
          "short_description": "...",
          "intended_use": "...",
          "when_to_use": "...",
          "expected_outputs": ["Scrum Backlog", "Sprint Planning"],
          "safety_review_stance": "balanced security, standard verification",
          "work_profile_summary": {
            "recommended_goal_modes": ["code_fix", "repo_analysis"],
            "playbook_hints": ["bugfix", "refactoring"],
            "capability_hints": ["coding", "testing"],
            "governance_profile": {
              "label": "Balanced with verification",
              "hint": "Execution remains delivery-oriented but expects explicit verification before closure."
            }
          },
          "goal_modes": ["code_fix", "repo_analysis"],
          "playbooks": ["bugfix", "refactoring"],
          "is_standard_blueprint": true,
          "entry_recommended": true
        }
      ]
    }
  }
  ```

### Blueprints auflisten
- **URL:** `/teams/blueprints`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rueckgabe:** `{"status": "success", "data": [{"id":"...","name":"Scrum","roles":[...],"artifacts":[...]}]}`

### Blueprint Details
- **URL:** `/teams/blueprints/<blueprint_id>`
- **Methode:** `GET`
- **Auth erforderlich:** Ja

### Blueprint Work-Profile lesen
- **URL:** `/teams/blueprints/<blueprint_id>/work-profile`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Beschreibung:** Liefert ein direkt nutzbares Arbeitsprofil fuer den Blueprint inklusive empfohlener Goal-Modi, Playbooks, Policy-Profilen und role-basierten Capability-Hinweisen.
- **Rueckgabe (Auszug):**
  ```json
  {
    "status": "success",
    "data": {
      "blueprint_id": "...",
      "blueprint_name": "Scrum-OpenCode",
      "goal_modes": ["code_fix", "docker_compose_repair", "code_review"],
      "playbooks": ["bugfix", "refactoring", "incident"],
      "recommended_action_pack_capabilities": ["file_read", "shell_exec"],
      "policy_profiles": [{"title": "OpenCode Scrum Default Policy", "payload": {"security_level": "balanced"}}]
    }
  }
  ```

### Blueprint erstellen
- **URL:** `/teams/blueprints`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `TeamBlueprintCreateRequest`

### Blueprint aktualisieren
- **URL:** `/teams/blueprints/<blueprint_id>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `TeamBlueprintUpdateRequest`
- **Verhalten:** Child-Persistierung ist diff-basiert; unveraenderte Rollen/Artefakte behalten ihre IDs.

### Blueprint loeschen
- **URL:** `/teams/blueprints/<blueprint_id>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja (Admin)
- **Verhalten:** Referenzierte Blueprints werden nicht geloescht; bei bestehenden Team-Referenzen antwortet die API mit `409 blueprint_in_use`.

### Blueprint-Audit und Seed-Reconcile

- Seed-Blueprints werden vor List-/Detail-Antworten deterministisch mit den Code-Seeds abgeglichen.
- Audit-Events `team_blueprint_created`, `team_blueprint_updated` und `team_blueprint_reconciled` enthalten differenzierte Change-Sets fuer:
  - `blueprint_fields`
  - `roles.created|updated|deleted`
  - `artifacts.created|updated|deleted`

### Blueprint-Bundle exportieren
- **URL:** `/teams/blueprints/<blueprint_id>/bundle`
- **Methode:** `GET`
- **Auth erforderlich:** Ja (Admin)
- **Query:** `mode=full|split`, optional `parts=blueprint,templates,team`, optional `team_id=<team_id>`, optional `include_members=true|false`
- **Rueckgabe:** Versioniertes JSON-Bundle mit `schema_version`, `mode`, `parts`, `blueprint`, `templates`, optional `team` und `bundle_metadata`.
- **Hinweise:**
  - `mode=full` exportiert standardmaessig den Blueprint inklusive referenzierter Templates; mit `team_id` wird zusaetzlich die Team-Konfiguration eingebettet.
  - `mode=split` erlaubt Teil-Exporte pro Komponente. Rollen und Artefakte bleiben bewusst im `blueprint`-Teil gebuendelt, damit Referenzen konsistent bleiben.

### Blueprint-Bundle importieren
- **URL:** `/teams/blueprints/import`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `TeamBlueprintBundleImportRequest`
- **Wichtige Felder:**
  - `conflict_strategy`: `fail|skip|overwrite`
  - `dry_run`: `true|false`
  - `bundle.schema_version`: aktuell `1.0`
  - `bundle.mode`: `full|split`
  - `bundle.parts`: bei `split` z. B. `["templates"]`, `["blueprint"]` oder `["blueprint","team"]`
- **Verhalten:**
  - `dry_run=true` schreibt nichts persistent und liefert eine `diff`-Antwort mit `create|update|skip|unchanged|conflict` pro Objektklasse.
  - `overwrite` ist idempotent fuer denselben Bundle-Inhalt.
  - Template-, Blueprint- und Team-Referenzen werden portabel ueber Namen aufgeloest; fehlende Referenzen werden als API-Fehler zurueckgegeben.

### Team aus Blueprint instanziieren
- **URL:** `/teams/blueprints/<blueprint_id>/instantiate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:** `TeamBlueprintInstantiateRequest`
- **Beschreibung:** Erzeugt ein Team, materialisiert Start-Artefakte und speichert die verwendete Definition in `team.blueprint_snapshot`.

### Teams auflisten
- **URL:** `/teams`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": [...]}`
- **Hinweis:** Team-Payloads enthalten additiv `user_lifecycle_state` mit vereinfachten Labels fuer den Standardmodus (`standard`, `customized`, `outdated`) sowie `label`/`hint`.

### Team erstellen
- **URL:** `/teams`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Body:** `TeamCreateRequest`
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Team aktualisieren
- **URL:** `/teams/<team_id>`
- **Methode:** `PATCH`
- **Auth erforderlich:** Ja
- **Body:** `TeamUpdateRequest`
- **Rückgabe:** `{"status": "success", "data": {...}}`

### Team löschen
- **URL:** `/teams/<team_id>`
- **Methode:** `DELETE`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "deleted"}}`

### Team aktivieren
- **URL:** `/teams/<team_id>/activate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Rückgabe:** `{"status": "success", "data": {"status": "activated"}}`

### Scrum Team Setup (Shortcut)
- **URL:** `/teams/setup-scrum`
- **Methode:** `POST`
- **Beschreibung:** Legacy-Shortcut, der intern den Seed-Blueprint `Scrum` instanziiert.
- **Body:** `{"name": "Team Name"}`
- **Rückgabe:** `{"status": "success", "message": "...", "data": {"team": {...}}}`

### Team-Typen auflisten
- **URL:** `/teams/types`
- **Methode:** `GET`
- **Rückgabe:** `{"status": "success", "data": [...]}`

### Rollen auflisten
- **URL:** `/teams/roles`
- **Methode:** `GET`
- **Rückgabe:** `{"status": "success", "data": [...]}`

---

## Auto-Planner (Goal-basierte Task-Generierung)

Der Auto-Planner analysiert High-Level-Ziele und generiert automatisch strukturierte Subtasks.

### Auto-Planner Status
- **URL:** `/tasks/auto-planner/status`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "enabled": false,
      "auto_followup_enabled": true,
      "max_subtasks_per_goal": 10,
      "default_priority": "Medium",
      "auto_start_autopilot": true,
      "llm_timeout": 30,
      "stats": {
        "goals_processed": 0,
        "tasks_created": 0,
        "followups_created": 0,
        "errors": 0
      }
    }
  }
  ```

### Auto-Planner konfigurieren
- **URL:** `/tasks/auto-planner/configure`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:**
  ```json
  {
    "enabled": true,
    "auto_followup_enabled": true,
    "max_subtasks_per_goal": 10,
    "default_priority": "Medium",
    "auto_start_autopilot": true,
    "llm_timeout": 30
  }
  ```
- **Rückgabe:** Aktualisierte Konfiguration

### Goal planen und Tasks erstellen
- **URL:** `/tasks/auto-planner/plan`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Analysiert ein Goal mit dem LLM und erstellt automatisch Subtasks.
- **Body:**
  ```json
  {
    "goal": "Implementiere ein User-Login-System mit JWT-Authentifizierung",
    "context": "Verwende Flask und PostgreSQL",
    "team_id": "optional-team-id",
    "parent_task_id": "optional-parent-id",
    "create_tasks": true
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "subtasks": [
        {"title": "...", "description": "...", "priority": "High"}
      ],
      "created_task_ids": ["goal-abc123", "goal-def456"],
      "raw_response": null
    }
  }
  ```

### Task auf Folgeaufgaben analysieren
- **URL:** `/tasks/auto-planner/analyze/<task_id>`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Analysiert einen abgeschlossenen Task auf natürliche Folgeaufgaben.
- **Body:** (optional)
  ```json
  {
    "output": "Überschreibt die Task-Ausgabe",
    "exit_code": 0
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "followups_created": [
        {"id": "followup-123", "title": "Tests schreiben", "priority": "Medium"}
      ],
      "analysis": {
        "task_complete": true,
        "needs_review": false,
        "suggestions": ["Dokumentation ergänzen"]
      }
    }
  }
  ```

---

## Trigger-System (Webhooks)

Das Trigger-System ermöglicht die automatische Task-Erstellung aus externen Quellen.

### Trigger-Status
- **URL:** `/triggers/status`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "enabled_sources": ["generic", "github"],
      "configured_handlers": ["generic", "github"],
      "webhook_secrets_configured": ["github"],
      "ip_whitelists": {
        "github": ["192.168.1.0/24", "10.0.0.1"],
        "slack": ["10.0.0.5"]
      },
      "rate_limits": {
        "github": {"max_requests": 60, "window_seconds": 60},
        "slack": {"max_requests": 30, "window_seconds": 60}
      },
      "stats": {
        "webhooks_received": 10,
        "tasks_created": 8,
        "rejected": 2,
        "rate_limited": 1,
        "ip_blocked": 0
      },
      "auto_start_planner": true
    }
  }
  ```

### Trigger konfigurieren
- **URL:** `/triggers/configure`
- **Methode:** `POST`
- **Auth erforderlich:** Ja (Admin)
- **Body:**
  ```json
  {
    "enabled_sources": ["generic", "github", "slack"],
    "webhook_secrets": {
      "github": "your-webhook-secret",
      "slack": "another-secret"
    },
    "ip_whitelists": {
      "github": ["192.168.1.0/24", "10.0.0.1"],
      "slack": ["10.0.0.5"]
    },
    "rate_limits": {
      "github": {"max_requests": 60, "window_seconds": 60},
      "slack": {"max_requests": 30, "window_seconds": 60}
    },
    "auto_start_planner": true
  }
  ```
- **Sicherheitsparameter:**
  - `ip_whitelists` (optional): Dict mit Source -> Liste erlaubter IPs. Leere Liste = alle IPs erlaubt.
  - `rate_limits` (optional): Dict mit Source -> `{max_requests, window_seconds}`. Default: 60 Requests / 60 Sekunden.
- **Rückgabe:** Aktualisierte Konfiguration

### Webhook empfangen
- **URL:** `/triggers/webhook/<source>`
- **Methode:** `POST`
- **Auth erforderlich:** Nein (Signatur-Validierung optional)
- **Beschreibung:** Empfängt Webhooks von externen Quellen.
- **Header:**
  - `X-Hub-Signature-256`: HMAC-SHA256 Signatur (falls Secret konfiguriert)
- **Body (Beispiel generic):**
  ```json
  {
    "title": "Bug Report: Login fehlschlägt",
    "description": "Der Login-Button reagiert nicht...",
    "priority": "High",
    "tags": ["bug", "auth"]
  }
  ```
- **Body (Beispiel mehrere Tasks):**
  ```json
  {
    "tasks": [
      {"title": "Task 1", "description": "...", "priority": "High"},
      {"title": "Task 2", "description": "...", "priority": "Medium"}
    ]
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "status": "processed",
      "tasks_created": 2,
      "task_ids": ["trg-gene-abc123", "trg-gene-def456"]
    }
  }
  ```
- **Fehler-Codes:**
  - `401 invalid_signature` - Webhook-Signatur ungültig
  - `403 source_disabled` - Quelle ist deaktiviert
  - `403 ip_not_whitelisted` - Client-IP nicht in Whitelist
  - `429 rate_limit_exceeded` - Zu viele Requests von dieser IP

### Trigger testen
- **URL:** `/triggers/test`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Testet einen Trigger ohne Tasks zu erstellen.
- **Body:**
  ```json
  {
    "source": "generic",
    "payload": {"title": "Test Task", "description": "Nur ein Test"}
  }
  ```
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "source": "generic",
      "parsed_tasks": [{"title": "Test Task", "description": "Nur ein Test"}],
      "would_create": 1
    }
  }
  ```

### Unterstützte Webhook-Quellen

| Source | Beschreibung | Payload-Format |
|--------|--------------|----------------|
| `generic` | Allgemeine JSON-Webhooks | `{title, description, priority, tasks[]}` |
| `github` | GitHub Issues & PRs | GitHub Webhook Format |
| `slack` | Slack Events | Slack Event API Format |
| `jira` | Jira Issue Events | Jira Webhook Format |

---

## Evolution API

Die Evolution API stellt eine analyse- und proposal-zentrierte Integrationsstufe bereit.
Provider duerfen Vorschlaege erzeugen; Apply bleibt standardmaessig deaktiviert und muss spaeter explizit policy-gesteuert angebunden werden.

### Evolution Provider auflisten
- **URL:** `/evolution/providers`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Beschreibung:** Liefert registrierte Evolution-Provider, Capabilities und wirksame Evolution-Konfiguration.
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "providers": [
        {
          "provider_name": "example-provider",
          "version": "unknown",
          "status": "available",
          "capabilities": ["analyze", "propose"],
          "provider_metadata": {},
          "default": true
        }
      ],
      "config": {
        "enabled": true,
        "analyze_only": true,
        "apply_allowed": false
      }
    }
  }
  ```

### Task-Evolution Read-Model
- **URL:** `/tasks/<task_id>/evolution`
- **Methode:** `GET`
- **Auth erforderlich:** Ja
- **Beschreibung:** Liefert persistierte Evolution-Runs und Proposals fuer einen Task.
- **Rückgabe:** `run_count`, `proposal_count`, `runs[]`, `proposals[]`

### Task-Evolution Analyse starten
- **URL:** `/tasks/<task_id>/evolution/analyze`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Baut einen provider-neutralen `EvolutionContext`, fuehrt `analyze` ueber den aktiven Provider aus und persistiert Run und Proposals.
- **Body:**
  ```json
  {
    "provider_name": "example-provider",
    "objective": "Improve failed task handling",
    "trigger_type": "manual",
    "trigger_source": "manual_api",
    "reason": "Manual review requested",
    "context_options": {
      "audit_limit": 50,
      "verification_limit": 10,
      "artifact_limit": 20,
      "include_audit_details": false
    }
  }
  ```
- **Unterstuetzte Trigger-Typen:** `manual`, `verification_failure`, `error_threshold`, `periodic_review`, `policy_request`
- **Rückgabe:**
  ```json
  {
    "status": "success",
    "data": {
      "run_id": "uuid",
      "provider_name": "example-provider",
      "status": "completed",
      "proposal_ids": ["uuid"],
      "summary": "Analysis summary"
    }
  }
  ```
- **Audit Events:** `evolution_analysis_requested`, `evolution_analysis_completed`, `evolution_analysis_failed`

### Task-Evolution Proposal validieren
- **URL:** `/tasks/<task_id>/evolution/proposals/<proposal_id>/validate`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Fuehrt einen expliziten Validierungsschritt fuer ein persistiertes Proposal aus, sofern `validate_allowed=true`.
- **Audit Events:** `evolution_validation_requested`, `evolution_validation_completed`, `evolution_validation_failed`

### Task-Evolution Proposal Apply vorbereiten
- **URL:** `/tasks/<task_id>/evolution/proposals/<proposal_id>/apply`
- **Methode:** `POST`
- **Auth erforderlich:** Ja
- **Beschreibung:** Zweite Ausbaustufe fuer kontrolliertes Apply. Standardmaessig fail-closed ueber `apply_allowed=false`.
- **Policy:** `apply_allowed=true` ist erforderlich; `require_review_before_apply=true` blockiert review-pflichtige Proposals.
- **Audit Events:** `evolution_apply_requested`, `evolution_apply_completed`, `evolution_apply_failed`
