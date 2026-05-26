# AI-Snake Training Data Architecture

Diese Architektur beschreibt den local-first Trainingsdatenfluss der Operator-TUI AI-Snake.

## 1) Event Recording

```mermaid
flowchart LR
    A[Interactive Tick] --> B[Observation Event]
    B --> C[Prediction Event Recorder]
    C --> D[prediction_events.jsonl]
    C --> E[prediction_profile.active.json]
```

## 2) Pattern Mining

```mermaid
flowchart LR
    A[prediction_events.jsonl] --> B[mine_patterns_from_events]
    B --> C[learned_patterns.json]
    C --> D[ai_hint/human_explanation]
    C --> E[expires_at/status/confidence]
```

## 3) Import/Export mit Validierung

```mermaid
flowchart LR
    A[Training Bundle JSON] --> B[Schema Validation]
    B --> C[Checksum Validation]
    C --> D[Conflict Strategy]
    D --> E[Store Update]
    E --> F[Audit Log]
```

## 4) Worker Context + ai_hint

```mermaid
flowchart LR
    A[Active Profile] --> B[Context Envelope Builder]
    C[Learned Patterns] --> B
    B --> D[training_profile_ref]
    B --> E[active_pattern_refs]
    D --> F[Worker Request]
    E --> F
```

## 5) Security/Privacy Invarianten

```mermaid
flowchart TD
    A[Raw Notes / Sensitive Input] --> B[Normalization]
    B --> C[Metadata-only policy]
    C --> D[privacy_manifest]
    D --> E{Export default}
    E -->|public_ui/workspace/private_local| F[Included]
    E -->|sensitive_blocked| G[Excluded]
    H[Cloud boundary] --> I[training_context denied by default]
```

## Invarianten

- Local-first Speicherung im Benutzerprofilverzeichnis.
- Keine automatische Cloud-Übertragung von Trainingsdaten.
- `sensitive_blocked` wird im Standard-Export ausgeschlossen.
- Checksum-Mismatch blockiert Import außer bei explizitem `--ignore-checksum` (mit Audit-Log).
