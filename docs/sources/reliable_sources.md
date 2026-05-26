# Reliable Sources Model

## Prinzip

Ananta trennt pro Quelle zwei Ebenen:

- `fetch_source`: technische Nachladequelle (schnell, cachebar, aktualisierbar)
- `citation_source`: dokumentierte, zitierfähige Herkunft

Diese Trennung verhindert, dass technische Fetch-Details und Nachweisdaten vermischt werden.

## Datenfluss

```mermaid
flowchart LR
  A[SourceDescriptor] --> B[Fetcher/Downloader]
  B --> C[SourceSnapshot immutable]
  C --> D[Chunking/Ingest]
  D --> E[SourceReference per chunk]
  E --> F[Citation Formatter]
  F --> G[API + Angular Sources Center]
```

## Snapshot-Regeln

- Snapshots sind immutable.
- Neue Refreshes erzeugen neue `snapshot_id`.
- Identische Inhalte werden als `duplicate` markiert (nicht als neuer `indexed` Snapshot).

## UI / Angular Sources Center

- Route: `Sources`
- Zeigt pro Quelle: Typ, Trust-Level, latest Snapshot, Refresh-Aktion, Citation.
- Smartphone/Android: Card-Layout statt breiter Tabellen; kein horizontales Scrollen nötig.

## Betrieb / Operator-TUI

- `:sources list`
- `:sources refresh <source-id> [--dry-run]`
- `:sources snapshots <source-id>`
- `:sources cite <source-id>`
- `:sources cache <source-id> [clear]`
