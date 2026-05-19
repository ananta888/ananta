# Ananta Template-/Rollen-/Overlay-Architektur

Status: Entwurf / Analyse v1.1

Ziel dieser Datei: das aktuelle Template-, Rollen-, Blueprint-, Overlay-, Prompt- und Prompt-Evolver-System von Ananta verständlich darstellen und daraus konkrete Architekturverbesserungen ableiten.

Wichtig: Der Dateiname `templeate` folgt absichtlich der angefragten Schreibweise.

---

## 1. Kurzüberblick

Ananta hat mehrere Ebenen, die zusammen den späteren Worker-Prompt und die Task-Ausführung beeinflussen:

1. **Goal / Mode / Mode Data**  
   fachlicher Auftrag des Users.

2. **Goal Config / Workflow Effective**  
   scoped Konfiguration für Planning, Routing, LLM, Policies und Execution.

3. **Planning Templates**  
   deterministische Subtask-Vorlagen aus dem Planning-Katalog.

4. **Planning Prompt Registry / Planning Prompt Evolver**  
   Prompt-Versionen für das Erzeugen von Plänen. Der Evolver darf nur diese Planning-Prompt-Schicht verbessern.

5. **Blueprints / Blueprint Roles / Blueprint Artifacts**  
   Team- und Rollenstruktur mit Task-Artifacts, Templates und Defaults.

6. **Plan / PlanNodes**  
   materialisierte Planstruktur mit Rationale, Retrieval-Hints, Verification-Spec und Blueprint-Provenance.

7. **Task / Worker Execution Context / Worker Execution Contract**  
   konkrete ausführbare Arbeitseinheit für Worker.

8. **Instruction Layer / User Profile / Overlay**  
   zusätzliche Prompt-Schichten für Stil, Sprache, Arbeitsmodus und taskbezogene Hinweise.

9. **ProposeContext / PromptContextBundle / ProposeStrategy**  
   finaler Kontext für LLM-/Worker-Strategien.

10. **Finaler Worker-Prompt**  
   strategieabhängig gebauter Prompt plus Tools, Policies und Context Bundle.

---

## 2. Gesamtfluss: Goal bis Worker

```mermaid
flowchart TD
    A[GoalDB<br/>goal/context/mode/mode_data/team_id] --> B[GoalConfigRuntimeService<br/>effective config]
    A --> C[PlanningService.plan_goal]

    B --> C
    C --> D[GoalPlanningIntent]
    C --> E[PlanningPolicy]

    E --> F[Strategy Order]
    F --> G[TemplatePlanningStrategy]
    F --> H[HubCopilotPlanningStrategy]
    F --> I[LLMPlanningStrategy]

    G --> G1[PlanningTemplateCatalog]
    G --> G2[BlueprintPlanningAdapter]
    G2 --> G3[BlueprintRoleDB config<br/>capability/risk/verification defaults]

    I --> I1[PlanningPromptRegistry]
    I --> I2[PlanningModelProfile]
    I --> I3[Domain/Behavior Hints]
    I --> I4[PlanningPromptEvolver<br/>after telemetry only]

    G1 --> J[Subtasks]
    G2 --> J
    H --> J
    I --> J

    J --> K[Planning Quality Gate]
    K -->|repair if needed| I
    K --> L[PlanDB]
    L --> M[PlanNodeDB<br/>rationale + verification_spec]

    M --> N[TaskLifecycleService]
    N --> O[worker_execution_context]
    N --> P[worker_execution_contract]
    O --> Q[TaskDB]
    P --> Q

    Q --> R[Assignment / Role Resolution]
    R --> S[resolve_task_role_template]
    S --> T[role/template context]

    Q --> U[InstructionLayerService]
    T --> U
    U --> V[InstructionStackArtifact<br/>target]

    Q --> W[TaskScopedExecutionService.propose_task_step]
    W --> X[ResearchContextBridge]
    W --> Y[ProposePolicyService]
    X --> Z[ProposeContext]
    Y --> Z
    V --> Z

    Z --> AA[ProposeStrategyOrchestrator]
    AA --> AB[ToolCallingLLMStrategy / JsonSchema / WorkerStrategy]
    AB --> AC[PromptContextBundleService]
    AC --> AD[Final System Prompt + base_prompt + tools]
    AD --> AE[LLM/Worker returns ExecutableProposal]
    AE --> AF[execute_task_step]
```

---

## 3. Zwei verschiedene Template-Systeme

### 3.1 Planning Template

Planning Templates erzeugen aus einem Goal direkt eine Liste von Subtasks. Sie sind deterministische Plan-Vorlagen.

```mermaid
flowchart TD
    A[Goal / Mode / mode_data.template_id] --> B[PlanningTemplateCatalog.resolve_template]
    B --> C{Match?}
    C -->|ja| D[Template Subtasks]
    C -->|nein| E[BlueprintPlanningAdapter]
    E -->|ja| F[Blueprint Artifact Subtasks]
    E -->|nein| G[LLMPlanningStrategy]
```

### 3.2 Role / Team / Blueprint Template

Rollen-/Team-/Blueprint-Templates beschreiben eher, **wie** eine Rolle arbeiten soll, nicht zwingend welche Subtasks entstehen.

```mermaid
flowchart TD
    A[Task] --> B{team_id + assigned_agent_url?}
    B -->|ja| C[TeamMemberDB]
    C --> D{custom_template_id?}
    D -->|ja| T1[TemplateDB from TeamMember]

    A --> E{assigned_role_id?}
    E -->|ja| F[Team.role_templates mapping]
    F --> T2[TemplateDB from Team Mapping]

    E --> G[RoleDB.default_template_id]
    G --> T3[Default Role Template]

    T1 --> Z[Resolved role/template context]
    T2 --> Z
    T3 --> Z
```

Priorität der Auflösung:

1. TeamMember `custom_template_id`
2. Team `role_templates[role_id]`
3. Role `default_template_id`

---

## 4. Blueprint Planning

```mermaid
flowchart TD
    A[Goal / query] --> B[match TeamBlueprintDB]
    B --> C[load BlueprintArtifactDB kind=task]
    B --> D[load BlueprintRoleDB]
    D --> E[resolve Template names]
    E --> F[role_template_hints]
    C --> G[build subtasks]
    F --> G
    G --> H[Subtask with blueprint_id, blueprint_role_name, template_name, role defaults]
```

Aus `BlueprintRoleDB.config` können übernommen werden:

- `capability_defaults`
- `risk_profile`
- `verification_defaults`

Diese Daten dürfen später nicht nur Prompt-Deko sein. Sie müssen in Routing, Verification, Context Policy und Worker Contract wirken.

---

## 5. PlanNode als zentrale Übersetzungsschicht

```mermaid
flowchart TD
    A[Subtask] --> B[infer task_kind]
    B --> C[derive retrieval_hints]
    A --> D[sanitize blueprint provenance]
    A --> E[sanitize role defaults]
    B --> F[derive required capabilities]
    E --> G[merge capabilities with blueprint defaults]
    E --> H[merge verification defaults]
    C --> I[PlanNode.rationale]
    D --> I
    G --> I
    H --> J[PlanNode.verification_spec]
    I --> K[PlanNodeDB]
    J --> K
```

`PlanNode.rationale` ist aktuell die wichtigste Brücke zwischen Planning und späterer Ausführung.

---

## 6. Task-Materialisierung

```mermaid
flowchart TD
    A[PlanNodeDB] --> B[TaskLifecycleService.materialize_from_plan_node]
    B --> C[extract rationale]
    C --> D[planning_provenance]
    C --> E[routing_hints]
    B --> F[load GoalDB]
    F --> G[goal context / output_dir / rag_sources / mode_data]
    B --> H[verification_spec]
    H --> I[worker_execution_contract]
    D --> J[worker_execution_context]
    E --> J
    G --> J
    I --> K[TaskDB extra_fields]
    J --> K
    K --> L[Task Queue]
```

---

## 7. Instruction Layer / Overlays

Die Instruction-Layer haben eine feste Reihenfolge:

```text
governance > blueprint_template > user_profile > task_overlay > task_input
```

```mermaid
flowchart TD
    A[Governance / Hub Policy] --> Z[Instruction Stack]
    B[Blueprint / Role Template] --> Z
    C[UserInstructionProfile] --> V[validate_user_layer_payload]
    D[InstructionOverlay] --> V
    E[Task Input] --> Z

    V -->|ok| Z
    V -->|forbidden directive| S[Suppress Layer]

    Z --> R[Rendered System Prompt + Diagnostics]
```

Erlaubter User-Einfluss:

- Stil
- Sprache
- Detailgrad
- Arbeitsmodus
- Formatierung

Verbotener User-Einfluss:

- Approval Policy
- Governance Policy
- Security Policy
- Allowed Tools
- Write Access
- Runtime Execution
- Model-/Provider-Override
- Context Scope / Cloud Policy

---

## 8. Aktuelle Schwachstelle

Der Code hat bereits einen `InstructionLayerService.assemble_for_task()`, der einen `rendered_system_prompt` bauen kann.

Aktuell wirkt es aber so, dass dieser gerenderte Systemprompt nicht konsequent in die finale Propose-/Worker-Prompt-Erzeugung integriert ist. Stattdessen tauchen Instruction-Informationen eher im `PromptContextBundle` als Meta/Diagnostics auf.

```mermaid
flowchart TD
    A[InstructionLayerService.assemble_for_task] --> B[rendered_system_prompt]
    A --> C[diagnostics]
    D[PromptContextBundleService] --> E[instruction_layers_present / instruction_selection]
    E --> F[Prompt Context Bundle]
    F --> G[ToolCallingLLMStrategy System Prompt]

    B -. missing deterministic integration .-> G
```

Soll:

```mermaid
flowchart TD
    A[TaskDB] --> B[resolve role template]
    A --> C[resolve instruction profile / overlay]
    B --> D[InstructionLayerService.assemble_for_task]
    C --> D
    D --> E[InstructionStackArtifact]
    E --> F[PromptContextBundle]
    E --> G[Strategy System Prompt]
    F --> G
    G --> H[LLM / Worker]
```

---

## 9. Prompt Evolver Boundary

Der `PlanningPromptEvolverService` ist kein Teil des finalen Worker-Prompt-Stacks. Er ist eine nachgelagerte Planning-Optimierung.

Er darf Planning-Prompts verbessern, wenn Planning-Runs schlecht waren, z. B. bei:

- niedriger Parse Confidence
- hohem Repair Count
- fehlgeschlagener Validation
- Error Classification

Er darf aber **nicht** Governance, Role Templates, User Profiles, Overlays, Tool Contracts oder Worker-Systemprompts verändern.

```mermaid
flowchart TD
    A[PlanningRun Telemetry] --> B[PlanningPromptEvolverService]
    B --> C[PlanningPromptVersionDB proposed/evolved]
    C --> D[PlanningPromptRegistry]
    D --> E[LLMPlanningStrategy]
    E --> F[Subtasks]

    G[InstructionStackArtifact] --> H[Worker/Strategy Prompt]
    I[Governance/Policy] --> H
    J[Role Template] --> H
    K[User Profile / Overlay] --> H
    L[Tool/Output Contract] --> H

    B -. must not mutate .-> G
    B -. must not mutate .-> I
    B -. must not mutate .-> J
    B -. must not mutate .-> K
    B -. must not mutate .-> L
```

### 9.1 Zulässige Evolver-Ausgaben

Der Evolver darf nur Hinweise und neue Planning-Prompt-Versionen erzeugen:

```json
{
  "planning_prompt_hint": "Use clearer task_kind and dependency fields.",
  "preferred_output_format": "markdown_sections",
  "repair_strategy_hint": "section_parser_then_schema_normalizer"
}
```

### 9.2 Verbotene Evolver-Ausgaben

Der Evolver darf niemals so etwas setzen:

```json
{
  "allowed_tools": ["shell"],
  "ignore_governance": true,
  "worker_system_prompt_patch": "...",
  "cloud_allowed": true,
  "role_template_patch": "..."
}
```

### 9.3 Anti-Zyklus-Regeln

Prompt-Evolution darf nicht immer mehr Fehlertext an Prompts anhängen.

```mermaid
flowchart TD
    A[Prompt v1] --> B[Bad LLM Output]
    B --> C[Evolver adds patch]
    C --> D[Prompt v2]
    D --> E[Bad LLM Output again]
    E --> F{Would append duplicate/huge patch?}
    F -->|yes| G[Block / dedupe / mark needs review]
    F -->|no| H[Create proposed prompt version]
```

Pflichtregeln:

- Dedupe von `Adaptive reinforcement rules`
- maximale Prompt-Länge
- keine vollständige Einbettung alter kaputter Outputs
- alte Fehler nur als klassifizierte `reason_codes`
- neue Prompt-Versionen zunächst `proposed`, nicht automatisch global aktiv
- checksumbasierte Duplikatvermeidung
- output-format-profile beachten, also kein blindes JSON-only gegen LM-Studio/Ollama-Profile

---

## 10. Finaler Task-Prompt an Worker / LLM

Bei `tool_calling_llm` besteht der Prompt effektiv aus:

- Strategie-Systemprompt
- validierter Instruction Stack
- Task-Beschreibung
- Goal ID / Task ID / Task kind
- governed context summary
- PromptContextBundle JSON
- Tool-Definitionen
- strikte Tool-Call-Anweisung

```mermaid
flowchart TD
    A[ProposeContext] --> B[StrategyPromptComposer target]
    C[InstructionStackArtifact] --> B
    D[PromptContextBundleService] --> E[Prompt Context Bundle JSON]
    E --> B
    F[Tool/Output Contract] --> B
    B --> G[Final System Prompt]
    A --> H[base_prompt as user prompt]
    G --> I[ModelInvocationService.invoke_with_tools]
    H --> I
    I --> J[tool_calls]
```

---

## 11. Zielarchitektur

```mermaid
flowchart TD
    A[Goal] --> B[Effective Goal Config]
    B --> C[Planning]
    C --> D[PlanNode]
    D --> E[Task]

    E --> F[RoleTemplateResolution]
    E --> G[InstructionSelection]
    F --> H[InstructionStackBuilder]
    G --> H

    H --> I[InstructionStackArtifact]
    I --> J[PromptContextBundle]
    I --> K[Strategy System Prompt]

    J --> L[ProposeContext]
    K --> L
    L --> M[ProposeStrategy]
    M --> N[Worker / LLM]

    O[PlanningPromptEvolver] --> P[PlanningPromptVersionDB]
    P --> C
    O -. boundary .-> I
    O -. boundary .-> K

    I --> Q[Audit / Diagnostics]
    J --> Q
    L --> Q
```

Eigenschaften:

- deterministic merge order
- policy-dominant
- auditierbar
- keine Tool-/Security-Eskalation durch User-Layer
- keine Worker-Prompt-Mutation durch Evolver
- finaler Prompt enthält wirklich den gerenderten Instruction Stack
- PromptContextBundle und Systemprompt nutzen dieselbe Quelle

---

## 12. Empfohlene Verbesserungen

### 12.1 InstructionStackArtifact einführen

```json
{
  "schema": "instruction_stack.v1",
  "task_id": "...",
  "goal_id": "...",
  "role_template": {},
  "applied_layers": [],
  "suppressed_layers": [],
  "rendered_system_prompt": "...",
  "diagnostics": {},
  "checksum": "..."
}
```

### 12.2 ProposeContext erweitern

`ProposeContext` sollte optional tragen:

- `instruction_stack`
- `rendered_system_prompt`
- `instruction_diagnostics`

### 12.3 ToolCallingLLMStrategy anpassen

Systemprompt sollte bestehen aus:

1. Governance/System rules
2. Role Template Prompt
3. User Profile
4. Task Overlay
5. Strategy-spezifische Tool-Call-Regeln
6. PromptContextBundle

### 12.4 PromptContextBundle anpassen

Nicht nur `instruction_layers_present`, sondern:

- stack checksum
- applied layers
- suppressed layers
- role/template ids
- compatibility status
- rendered prompt hash

### 12.5 Prompt Evolver begrenzen

Der Evolver darf ausschließlich Planning-Prompt-Versionen und Planning-Profile beeinflussen. Er darf keine Worker-Prompts, Role Templates, Overlays, Governance oder Tool Contracts patchen.

### 12.6 Planning und Execution klar trennen

Planning-Templates erzeugen Tasks.  
Role-Templates steuern Arbeitsweise.  
Instruction-Overlays steuern nur erlaubte Stil-/Arbeitsmodus-Präferenzen.  
Prompt Evolver optimiert nur Planning-Prompt-Versionen.

---

## 13. Wichtigste Regel

Der finale Prompt darf nie aus zufällig zusammengesetzten Strings entstehen.

Er sollte immer aus einem deterministischen, validierten und auditierbaren Stack kommen:

```text
Goal -> Plan -> Task -> RoleTemplate -> InstructionStack -> ProposeContext -> StrategyPrompt -> Worker
```

Der Prompt Evolver hängt daneben, nicht darunter:

```text
PlanningRun Telemetry -> Prompt Evolver -> PlanningPromptVersion -> zukünftige Planning-Runs
```
