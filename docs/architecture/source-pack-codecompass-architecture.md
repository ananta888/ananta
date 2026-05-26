# SourcePack + CodeCompass Architektur

## Überblick

Der Flow bleibt hub-zentriert: Source Packs liefern verwaltete Quellen, Snapshots und Bundle-Metadaten; Worker konsumieren nur kontrollierte Referenzen.

## Flow 1: SourcePack -> Descriptor -> Snapshot -> CodeCompassBundle

```mermaid
flowchart LR
  A[SourcePack ananta-dev-default] --> B[SourceDescriptors]
  B --> C[Refresh/Bootstrap]
  C --> D[SourceSnapshots immutable]
  D --> E[CodeCompassBundle metadata-only]
  E --> F[Citation Bundle]
  E --> G[Doctor Ready Check]
```

## Flow 2: Worker Request -> Retrieval Routing -> SourceReferences -> Provenance

```mermaid
flowchart LR
  Q[Worker/TUI Query] --> R[Routing Rules]
  R --> S1[Local Project Context]
  R --> S2[Eclipse/Keycloak Technical Sources]
  R --> S3[Wikipedia Fallback]
  S1 --> T[SourceReferences]
  S2 --> T
  S3 --> T
  T --> U[Answer + Provenance]
  U --> V[context_hash + trust_level + snapshot_id + bundle_id]
```

## Flow 3: Eclipse Plugin Development Context

```mermaid
flowchart TD
  P[Plugin-Frage: plugin.xml / MANIFEST.MF / JDT] --> X[SourcePack Query Preview]
  X --> Y[Eclipse Platform/PDE/JDT priorisiert]
  Y --> Z[CodeCompassBundle Referenzen]
  Z --> AA[Antwort mit Eclipse SourceReferences]
```

## Prioritätsregeln

1. Lokale Projektquellen
2. Offizielle technische Quellen (Eclipse/Keycloak)
3. Allgemeines Wissen (Wikipedia) als Ergänzung

## Testmatrix

| Bereich | Testfokus |
|---|---|
| Schema/Descriptor | SourcePack-Schema, Eclipse/Keycloak/Wikipedia-Referenzen, duplicate source_id rejection |
| Fixture-Bootstrap | Offline, deterministisch, Snapshot- und Bundle-Erzeugung |
| Routing | SWT/JFace -> Eclipse, JDT AST -> JDT, Realm/OIDC -> Keycloak, Allgemein -> optional Wikipedia |
| E2E | CLI/TUI Bootstrap + Doctor + Query Preview mit SourceReferences/Provenance |
