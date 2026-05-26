# Ananta Dev Default Source Pack

## Zweck

`ananta-dev-default` ist das vorkonfigurierte Quellenpaket für Entwicklungsfragen rund um Eclipse-Plugin-Entwicklung, Keycloak und allgemeines Hintergrundwissen.

Enthaltene Quellen:

- Eclipse Platform
- Eclipse JDT Core
- Eclipse PDE
- Keycloak Official Docs
- Wikimedia Wikipedia Initial Dump

## Bootstrap und Betrieb

```bash
# nur Planung/Validierung
ananta sources bootstrap ananta-dev-default --dry-run

# echter Bootstrap
ananta sources bootstrap ananta-dev-default

# Wikipedia bewusst auslassen
ananta sources bootstrap ananta-dev-default --skip-source wikimedia-wikipedia-initial-dump

# Bereitschaft prüfen
ananta sources doctor ananta-dev-default
ananta sources doctor ananta-dev-default --json
```

## Lizenz- und Attribution-Hinweise

- Eclipse-Quellen nutzen `EPL-2.0`.
- Wikipedia/Wikimedia nutzt `CC BY-SA 4.0` und benötigt Attribution.
- Keycloak ist im Default mit `license_unknown` markiert; je Policy gibt es Warnung oder Blockierung beim Bootstrap.
- Bootstrap erzeugt ein Citation Bundle (`source_pack_citation_bundle.v1`) für nachvollziehbare Quellenangaben.

## Typischer Flow für Eclipse-Plugin-Entwicklung

1. Source Pack per Dry-Run prüfen.
2. Bootstrap ausführen (optional ohne Wikipedia).
3. `ananta sources doctor` muss `ready` melden.
4. Entwicklungsfrage stellen, z. B. zu `plugin.xml`, `MANIFEST.MF`, OSGi oder JDT-AST.
5. Antwort/Preview enthält SourceReferences mit `source_pack_id`, `source_id`, `snapshot_id`, `trust_level`, `codecompass_bundle_id`.

Damit bleibt nachvollziehbar, ob Kontext aus Eclipse, Keycloak, Wikipedia oder lokalem Projektkontext stammt.
