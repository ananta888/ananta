# Ananta Mobile Architecture

Date: 2026-04-30
Scope: ANM-090

```mermaid
flowchart TD
    U[User] --> UI[Angular Mobile UI]
    UI --> VOX[VoxtralOfflineService]
    UI --> LLM[LlamaRuntimeService]

    VOX --> VPLUG[VoxtralOfflinePlugin]
    LLM --> LPLUG[LlamaCppRuntimePlugin]

    VPLUG --> PB[PermissionBroker\nDefault-Deny]
    VPLUG --> FS[Filesystem Sandbox]
    VPLUG --> NET[HTTPS Allowlist]
    VPLUG --> AUD[Audit Log]

    LPLUG --> JNI[JNI Bridge]
    JNI --> NATIVE[Native Runtime Stub\n(ananta_llama_runtime)]

    UI --> ADAPTER[MobileAgentRuntimeAdapter]
    ADAPTER --> LOCAL[Local Execution]
    ADAPTER --> REMOTE[Optional Remote Fallback]

    HUB[Hub Control Plane] --> ADAPTER
```

## Verantwortlichkeiten

- Hub: Planung, Routing, Governance.
- Mobile Runtime: lokale Ausfuehrung (Audio/Text) mit Policy-Gates.
- Adapter: Capability-Routing und Fallback-Entscheidung, ohne Orchestrierungswechsel.

## SOLID-Bezug

- SRP: UI, Runtime-Adapter, Native-Plugin und Security-Broker sind getrennt.
- DIP: UI haengt an Service-Abstraktionen, nicht an nativen Details.
