# Ananta Template-/Rollen-/Overlay-Architektur

Status: Entwurf / Analyse

Ziel dieser Datei: das aktuelle Template-, Rollen-, Blueprint-, Overlay- und Prompt-System von Ananta verständlich darstellen und daraus konkrete Architekturverbesserungen ableiten.

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

4. **Blueprints / Blueprint Roles / Blueprint Artifacts**  
   Team- und Rollenstruktur mit Task-Artifacts, Templates und Defaults.

5. **Plan / PlanNodes**  
   materialisierte Planstruktur mit Rationale, Retrieval-Hints, Verification-Spec und Blueprint-Provenance.

6. **Task / Worker Execution Context / Worker Execution Contract**  
   konkrete ausführbare Arbeitseinheit für Worker.

7. **Instruction Layer / User Profile / Overlay**  
   zusätzliche Prompt-Schichten für Stil, Sprache, Arbeitsmodus und taskbezogene Hinweise.

8. **ProposeContext / PromptContextBundle / ProposeStrategy**  
   finaler Kontext für LLM-/Worker-Strategien.

9. **Finaler Worker-Prompt**  
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
    U --> V[profile/overlay diagnostics or rendered prompt]

    Q --> W[TaskScopedExecutionService.propose_task_step]
    W --> X[ResearchContextBridge]
    W --> Y[ProposePolicyService]
    X --> Z[ProposeContext]
    Y --> Z

    Z --> AA[ProposeStrategyOrchestrator]
    AA --> AB[ToolCallingLLMStrategy / JsonSchema / WorkerStrategy]
    AB --> AC[PromptContextBundleService]
    AC --> AD[Final System Prompt + base_prompt + tools]
    AD --> AE[LLM/Worker returns ExecutableProposal]
    AE --> AF[execute_task_step]
```

---

## 3. Datenmodell-Überblick

```mermaid
classDiagram
    class GoalDB {
      goal
      context
      constraints
      acceptance_criteria
      execution_preferences
      workflow_defaults
      workflow_overrides
      workflow_effective
      mode
      mode_data
      team_id
    }

    class TeamDB {
      team_type_id
      blueprint_id
      role_templates
      blueprint_snapshot
    }

    class TeamMemberDB {
      team_id
      agent_url
      role_id
      blueprint_role_id
      custom_template_id
    }

    class RoleDB {
      name
      default_template_id
    }

    class TemplateDB {
      name
      prompt_template
    }

    class TeamBlueprintDB {
      name
      base_team_type_name
    }

    class BlueprintRoleDB {
      blueprint_id
      name
      template_id
      config
    }

    class BlueprintArtifactDB {
      blueprint_id
      kind
      title
      payload
    }

    class PlanDB {
      goal_id
      trace_id
      status
      planning_mode
      rationale
    }

    class PlanNodeDB {
      plan_id
      title
      description
      depends_on
      rationale
      verification_spec
      materialized_task_id
    }

    class TaskDB {
      title
      description
      task_kind
      required_capabilities
      worker_execution_context
      worker_execution_contract
      assigned_role_id
      assigned_agent_url
    }

    GoalDB --> TeamDB
    TeamDB --> TeamBlueprintDB
    TeamDB --> TeamMemberDB
    TeamMemberDB --> RoleDB
    TeamMemberDB --> TemplateDB
    RoleDB --> TemplateDB
    TeamBlueprintDB --> BlueprintRoleDB
    BlueprintRoleDB --> TemplateDB
    TeamBlueprintDB --> BlueprintArtifactDB
    GoalDB --> PlanDB
    PlanDB --> PlanNodeDB
    PlanNodeDB --> TaskDB
```

---

## 4. Zwei verschiedene Template-Systeme

### 4.1 Planning Template

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

**Funktion:**

- schnell
- deterministisch
- geeignet für bekannte Goal-Typen
- erzeugt PlanNodes ohne LLM

### 4.2 Role / Team / Blueprint Template

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

**Priorität der Auflösung:**

1. TeamMember `custom_template_id`
2. Team `role_templates[role_id]`
3. Role `default_template_id`

---

## 5. Blueprint Planning

Blueprints können Task-Artifacts und Rollen-Defaults liefern.

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

Dabei können aus `BlueprintRoleDB.config` übernommen werden:

- `capability_defaults`
- `risk_profile`
- `verification_defaults`

Diese Daten dürfen später nicht nur Prompt-Deko sein. Sie müssen in Routing, Verification, Context Policy und Worker Contract wirken.

---

## 6. PlanNode als zentrale Übersetzungsschicht

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

Typische Inhalte:

```json
{
  "task_kind": "coding",
  "retrieval_intent": "symbol_and_dependency_neighborhood",
  "required_context_scope": "module_and_related_symbols",
  "preferred_bundle_mode": "standard",
  "required_capabilities": ["coding", "analysis"],
  "blueprint_id": "...",
  "blueprint_role_name": "...",
  "template_name": "...",
  "blueprint_role_defaults": {
    "capability_defaults": {},
    "risk_profile": {},
    "verification_defaults": {}
  }
}
```

---

## 7. Task-Materialisierung

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

Der `worker_execution_context` ist der erste konkrete Ausführungskontext für Worker. Er enthält:

- `planning_provenance`
- `routing_hints`
- Goal-Kontext
- Workspace-Informationen
- Research/RAG Quellen
- optional repair foundation

Der `worker_execution_contract` enthält erwartete Artefakte und Verification-Vorgaben.

---

## 8. Instruction Layer / Overlays

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

---

## 9. Aktuelle Schwachstelle

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

**Soll:**

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

## 10. Finaler Task-Prompt an Worker / LLM

Bei `tool_calling_llm` besteht der Prompt effektiv aus:

- Systemprompt der Strategie
- Task-Beschreibung
- Goal ID / Task ID / Task kind
- governed context summary
- PromptContextBundle JSON
- Tool-Definitionen
- strikte Tool-Call-Anweisung

```mermaid
flowchart TD
    A[ProposeContext] --> B[ToolCallingLLMStrategy._build_system_prompt]
    A --> C[PromptContextBundleService.build_for_propose_context]
    C --> D[contract_summary]
    C --> E[context_summary]
    C --> F[policy_summary]
    D --> G[Prompt Context Bundle JSON]
    E --> G
    F --> G
    B --> H[System Prompt]
    G --> H
    A --> I[base_prompt as user prompt]
    H --> J[ModelInvocationService.invoke_with_tools]
    I --> J
    J --> K[tool_calls]
```

---

## 11. Zielarchitektur

Die Zielarchitektur sollte die Prompt-Schichten nicht nur als lose Strings behandeln, sondern als auditierbare Artefakte.

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

    I --> O[Audit / Diagnostics]
    J --> O
    L --> O
```

Eigenschaften:

- deterministic merge order
- policy-dominant
- auditierbar
- keine Tool-/Security-Eskalation durch User-Layer
- finaler Prompt enthält wirklich den gerenderten Instruction Stack
- PromptContextBundle und Systemprompt nutzen dieselbe Quelle

---

## 12. Empfohlene Verbesserungen

### 12.1 InstructionStackArtifact einführen

Ein explizites Artefakt, das enthält:

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

### 12.5 Planning und Execution klar trennen

Planning-Templates erzeugen Tasks.  
Role-Templates steuern Arbeitsweise.  
Instruction-Overlays steuern nur erlaubte Stil-/Arbeitsmodus-Präferenzen.

Diese Trennung sollte in Code, Doku und Tests sichtbar sein.

---

## 13. Wichtigste Regel

Der finale Prompt darf nie aus zufällig zusammengesetzten Strings entstehen.

Er sollte immer aus einem deterministischen, validierten und auditierbaren Stack kommen:

```text
Goal -> Plan -> Task -> RoleTemplate -> InstructionStack -> ProposeContext -> StrategyPrompt -> Worker
```
