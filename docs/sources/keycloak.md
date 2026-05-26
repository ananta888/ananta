# Keycloak Source

## Offizielle Quelle

- Documentation: https://www.keycloak.org/documentation
- Guides: https://www.keycloak.org/guides

`source_id`: `keycloak-official-docs`

## Refresh

- Standardmäßig über `refresh_interval` im Descriptor.
- Manuell via API: `POST /sources/keycloak-official-docs/refresh`
- Manuell via TUI: `:sources refresh keycloak-official-docs`

## Zitierformat

Die Citation enthält:

- Titel und Publisher
- canonical URL
- `retrieved_at`
- `snapshot_id` + Snapshot-Hash
- Lizenzhinweis
- Dokumentationsversion (wenn verfügbar)

## Testmodus

E2E-Fixture: `tests/fixtures/sources/keycloak/mini.html`  
Der Test läuft offline und prüft Snapshot, Chunking und Citation.

