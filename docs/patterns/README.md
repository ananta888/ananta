# Deterministic Pattern Library

The pattern library lets workers generate structurally validated, reproducible
code from a catalog of GoF design patterns.  LLMs select and parametrize a
pattern; the local toolchain validates, renders, and gates the result.

See [ADR-deterministic-pattern-library](../decisions/ADR-deterministic-pattern-library.md)
for the design rationale.

## Quick Start

### 1. Validate the catalog

```bash
python scripts/validate_pattern_catalog.py
# Add --render-examples to dry-run java.strategy / python.strategy / ts.strategy
python scripts/validate_pattern_catalog.py --render-examples
```

### 2. Render a pattern (Python API)

```python
from pathlib import Path
from agent.services.pattern_template_renderer import PatternTemplateRenderer, TemplateFile
from agent.services.pattern_registry import get_registry

registry = get_registry()
entry = registry.get("python.strategy")          # catalog entry
renderer = PatternTemplateRenderer()

# Load template files from the catalog entry
templates = [
    TemplateFile(
        template_name=t["name"],
        output_path=t["path"].split("/")[-1].replace(".tmpl", ""),
        content=Path(t["path"]).read_text(),
    )
    for t in entry["templates"]
]

manifest = renderer.render(
    pattern_plan={
        "pattern_id": "python.strategy",
        "language": "python",
        "parameters": {"context_class": "Order"},
    },
    templates=templates,
    target_root="/tmp/demo",
)
print(f"rendered {len(manifest.files)} files, manifest hash: {manifest.manifest_sha256[:12]}")
```

### 3. Gate-check the rendered output

```python
from agent.services.pattern_gate_service import get_pattern_gate_service

gate = get_pattern_gate_service()
result = gate.check(
    pattern_id="python.strategy",
    language="python",
    output_files=[f.output_path for f in manifest.files],
    workspace_root=Path("/tmp/demo"),
)
print("passed" if result.passed else f"failed: {result.failed_checks}")
```

---

## PatternPlan Format

A PatternPlan is a JSON object that a worker (or LLM) submits to request
pattern-guided code generation.  The hub validates it locally before the
renderer runs.

```json
{
  "pattern_id": "python.strategy",
  "language": "python",
  "target": "src/order/strategy",
  "roles": {
    "Strategy": "OrderStrategy",
    "ConcreteStrategy": ["ShippingStrategy", "DiscountStrategy"],
    "Context": "OrderContext"
  },
  "parameters": {
    "context_class": "Order"
  },
  "output_files": [
    "strategy_protocol.py",
    "strategy_context.py",
    "strategy_primary.py",
    "strategy_secondary.py",
    "test_strategy.py"
  ],
  "tests": ["test_strategy.py"],
  "selection_reason": "Order processing needs swappable pricing algorithms at runtime"
}
```

**Validation rules:**
- `pattern_id` must exist in the registry
- `language` must be `java`, `python`, or `typescript`
- `output_files` must be relative paths; `..` and absolute paths are rejected
- `tests` must be non-empty (unless `mode: explain_only`)
- `parameters` must match the pattern's declared parameter schema
- Unknown `parameters` keys are rejected (default policy)

---

## Blueprint Integration: pattern_hints

A blueprint workflow step can declare which patterns a worker may use:

```json
{
  "id": "implement_order_service",
  "role": "developer",
  "task_kind": "coding",
  "title": "Implement Order Service",
  "pattern_hints": {
    "allowed_patterns": ["python.strategy", "python.function_stub"],
    "preferred_patterns": ["python.strategy"],
    "language_targets": ["python"],
    "require_tests": true
  }
}
```

When a task is created from this step, the hub writes two files into the worker's
workspace:
- `.ananta/patterns/pattern-selection-contract.json` — machine-readable contract
- `.ananta/patterns/allowed-patterns.md` — human-readable summary

The worker prompt references these files; the full catalog is **not** included
inline, keeping the prompt short.

**Constraint rules for pattern_hints:**
- `preferred_patterns` must be a subset of `allowed_patterns` (if both are set)
- Pattern IDs must match `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`
- Unknown IDs are rejected at catalog-seed time (no silent ignore)

---

## Examples

### Java Strategy — Order Processing

**Scenario:** An order service needs interchangeable pricing strategies.

**PatternPlan:**
```json
{
  "pattern_id": "java.strategy",
  "language": "java",
  "parameters": {
    "context_class": "Order",
    "package_name": "com.example.order.strategy"
  }
}
```

**Generated files:**
- `Strategy.java` — `interface OrderStrategy { void execute(Order order); }`
- `Context.java` — `class OrderContext { private OrderStrategy strategy; … }`
- `PrimaryStrategy.java` — first concrete implementation
- `SecondaryStrategy.java` — second concrete implementation
- `StrategyTest.java` — JUnit test scaffold

---

### Python Strategy — Payment Gateway

**Scenario:** Payment processing needs a swappable gateway adapter.

**PatternPlan:**
```json
{
  "pattern_id": "python.strategy",
  "language": "python",
  "parameters": {
    "context_class": "Payment"
  }
}
```

**Generated files (dry-run sha256 stable across runs):**
- `strategy_protocol.py` — `class PaymentStrategy(Protocol)`
- `strategy_context.py` — `class PaymentContext`
- `strategy_primary.py` — `class PrimaryStrategy`
- `strategy_secondary.py` — `class SecondaryStrategy`
- `test_strategy.py` — pytest module

---

### TypeScript Strategy — Checkout Flow

**Scenario:** Checkout needs pluggable discount calculation.

**PatternPlan:**
```json
{
  "pattern_id": "ts.strategy",
  "language": "typescript",
  "parameters": {
    "context_class": "Checkout"
  }
}
```

**Generated files:**
- `strategy.types.ts` — `interface CheckoutStrategy`
- `context.ts` — `class CheckoutContext`
- `primary.strategy.ts` — `class PrimaryStrategy implements CheckoutStrategy`
- `secondary.strategy.ts` — `class SecondaryStrategy implements CheckoutStrategy`
- `context.test.ts` — Vitest suite

---

## Security Notes

| Risk | Mitigation |
|------|------------|
| Path traversal in output_files | Renderer rejects `..` and absolute paths |
| Template injection | Uses `string.Template`; no expression evaluation |
| Singleton misuse | `singleton_guarded` requires explicit `allow_risky_patterns: true` |
| Unknown pattern IDs | Rejected by registry lookup before render |
| LLM inventing pattern schemas | `PatternProposalNormalizer` strips unknown keys; only registry IDs are valid |

---

## Catalog Maintenance

Add a new pattern by:
1. Adding a JSON entry to `schemas/patterns/pattern_catalog.v1.json`
2. Adding template files under `config/patterns/templates/<language>/<pattern>/` (code patterns only — notation patterns are template-less)
3. Running `python scripts/validate_pattern_catalog.py` to verify
4. Writing a gate checker in `agent/services/pattern_gate_service.py` (optional but recommended)

Pattern IDs follow `<language_prefix>.<pattern_name>` convention
(e.g. `java.strategy`, `python.function_stub`, `ts.vitest_scaffold`).
Language-agnostic patterns use a category prefix (e.g. `cli.retry_wrap`,
`workflow.sequential_emit`).

---

## Diagram Notation Patterns (NOT-001 .. NOT-006)

Diagram notation patterns are a parallel pattern family that produces
Mermaid or BPMN 2.0 source from a structured payload. Unlike code
patterns (which substitute parameters into template files), notation
patterns are **deterministic generators**: a structured input is
transformed into a complete diagram source string in one pass.

### Why a separate pattern family?

Code patterns and notation patterns have different shapes:

| Aspect              | Code pattern                    | Notation pattern                |
|---------------------|----------------------------------|----------------------------------|
| Output              | Multiple files                  | Single source file (`.mmd` / `.bpmn`) |
| Generator           | `string.Template` substitution  | Pure deterministic function     |
| Validation          | Template-safety + path checks   | Structural checks (XML / diagram) |
| File templates      | Required                        | None                            |
| Required artifacts  | Source files                    | Diagram source                  |

Mixing them would violate SRP and force either the code renderer to
grow a payload parser or the notation renderer to grow a template
engine. Instead, both renderers plug into the same
`PatternExecutionContextResolver` which dispatches by catalogue
category.

### Catalogue entries

Eight notation patterns are available:

| Pattern ID                | Notation    | Diagram view       | File          |
|---------------------------|-------------|---------------------|---------------|
| `mermaid.class`           | Mermaid     | UML2 Class          | `diagram.mmd` |
| `mermaid.sequence`        | Mermaid     | UML2 Sequence       | `diagram.mmd` |
| `mermaid.state`           | Mermaid     | UML2 State Machine  | `diagram.mmd` |
| `mermaid.usecase`         | Mermaid     | UML2 Use-Case       | `diagram.mmd` |
| `mermaid.activity`        | Mermaid     | UML2 Activity       | `diagram.mmd` |
| `bpmn.process`            | BPMN 2.0    | Single process      | `process.bpmn` |
| `bpmn.pool_lane`          | BPMN 2.0    | Process + lanes     | `process.bpmn` |
| `bpmn.collaboration`      | BPMN 2.0    | Multi-pool + msgs   | `collaboration.bpmn` |

The catalogue declares `category: "diagram_notation"` and a language of
`mermaid` or `bpmn`. The resolver looks up the catalogue entry and
routes to `NotationRenderer` when `category == "diagram_notation"`.

### Quick Start

#### 1. Render a Mermaid class diagram (Python API)

```python
from agent.services.notation_renderer import get_notation_renderer

r = get_notation_renderer()
artifact = r.render(pattern_plan={
    "pattern_id": "mermaid.class",
    "language": "mermaid",
    "parameters": {
        "diagram_title": "Order Strategy",
        "direction": "LR",
        "classes": [
            {"name": "OrderStrategy", "stereotype": "interface",
             "methods": ["calculate(order: Order): Money"]},
            {"name": "StandardPricing",
             "methods": ["calculate(order: Order): Money"]},
        ],
        "relationships": [
            {"type": "realization",
             "from": "StandardPricing", "to": "OrderStrategy"},
            {"type": "association",
             "from": "Order", "to": "OrderStrategy",
             "to_label": "1"},
        ],
    },
})
print(artifact.source)
# classDiagram
# %% Order Strategy
#   direction LR
#   class OrderStrategy {
#     <<interface>>
#     calculate(order: Order): Money {abstract}
#   }
#   ...
print(f"sha256={artifact.sha256} manifest={artifact.manifest_sha256}")
artifact_on_disk = r.render(
    pattern_plan={...}, target_root="/tmp/out",
)
```

#### 2. Render a BPMN 2.0 process

```python
artifact = r.render(pattern_plan={
    "pattern_id": "bpmn.process",
    "language": "bpmn",
    "parameters": {
        "definitions_id": "Definitions_1",
        "process_id": "Process_OrderFulfillment",
        "process_name": "Order Fulfillment",
        "elements": [
            {"type": "startEvent", "id": "StartEvent_1",
             "name": "Order Received"},
            {"type": "userTask", "id": "Task_Pick",
             "name": "Pick Items"},
            {"type": "exclusiveGateway", "id": "Gateway_Stock",
             "name": "Stock Available?"},
            {"type": "serviceTask", "id": "Task_Ship",
             "name": "Ship Order"},
            {"type": "endEvent", "id": "EndEvent_Done",
             "name": "Done"},
        ],
        "flows": [
            {"id": "Flow_1", "sourceRef": "StartEvent_1",
             "targetRef": "Task_Pick"},
            {"id": "Flow_2", "sourceRef": "Task_Pick",
             "targetRef": "Gateway_Stock"},
            {"id": "Flow_3", "sourceRef": "Gateway_Stock",
             "targetRef": "Task_Ship",
             "conditionExpression": "${stockAvailable}"},
            {"id": "Flow_4", "sourceRef": "Task_Ship",
             "targetRef": "EndEvent_Done"},
        ],
    },
})
```

The emitted XML uses the OMG BPMN 2.0 namespace
`http://www.omg.org/spec/BPMN/20100524/MODEL` and parses with any
compliant engine (Camunda, bpmn.io, etc.).

#### 3. Run the gate

```python
from agent.services.pattern_gate_service import get_pattern_gate_service

gate = get_pattern_gate_service()
result = gate.check(
    pattern_id="mermaid.class",
    language="mermaid",
    output_files=[artifact.output_filename],
    workspace_root=Path("/tmp/out"),
)
print("passed" if result.passed else f"failed: {result.failed_checks}")
```

### Notation-specific invariants

Every notation pattern guarantees:

* **Determinism** — identical inputs produce byte-identical output.
* **No templates** — there are no `@@var@@` placeholders; notation
  output is generated from the structural payload only.
* **No test files** — notation patterns are not compiled and
  executed, so the gate service skips the `has_test_file` check.
* **Path safety** — output filenames default to `diagram.mmd` /
  `process.bpmn` / `collaboration.bpmn` and are rejected if they
  escape `target_root`.

### UML2 conformance (Mermaid)

The Mermaid generator uses canonical UML2 glyphs:

| Relationship        | Mermaid arrow |
|---------------------|----------------|
| Inheritance         | `<\|--`         |
| Realization         | `..\|>`         |
| Composition         | `*--`           |
| Aggregation         | `o--`           |
| Association         | `-->`           |
| Dependency          | `..>`           |
| Plain link          | `--`            |

Interface methods are emitted with `{abstract}`. Multiplicity is
expressed with quoted labels on either side of the arrow
(e.g. `"1" --> "1..*"`).

### BPMN 2.0 conformance

The BPMN generator emits well-formed XML conforming to the OMG BPMN
2.0 specification:

* `bpmn:definitions` root with the MODEL namespace
  (`http://www.omg.org/spec/BPMN/20100524/MODEL`) and DI / DC / DI
  namespaces.
* Flow elements: `startEvent`, `endEvent`, `userTask`,
  `serviceTask`, `scriptTask`, `manualTask`, `task`,
  `exclusiveGateway`, `inclusiveGateway`, `parallelGateway`,
  `eventBasedGateway`.
* Sequence flows with `sourceRef` / `targetRef` and optional
  `conditionExpression` (for exclusive gateway branches).
* Pool/Lane patterns emit a single `bpmn:laneSet` whose
  `flowNodeRef` entries cover every flow element exactly once.
* Collaboration patterns emit `bpmn:collaboration` with one
  `bpmn:participant` per process and one `bpmn:messageFlow` per
  cross-pool message.
* A minimal `bpmndi:BPMNDiagram` (with `BPMNShape` and `BPMNEdge`)
  is included so the file opens in any BPMN modelling tool
  (Camunda Modeler, bpmn.io).

### Blueprint integration: notation_hints

Blueprint steps that target a diagram view can declare which notation
patterns a worker may use:

```json
{
  "id": "render_class_diagram",
  "role": "developer",
  "task_kind": "diagram_mermaid",
  "title": "Render class diagram",
  "notation_hints": {
    "allowed_notations": ["mermaid.class", "mermaid.sequence"],
    "preferred_notations": ["mermaid.class"],
    "default_notation": "mermaid.class"
  }
}
```

When a task is created from this step, the worker workspace gets two
files:

* `.ananta/notation/notation-selection-contract.json` — machine-readable
  contract (schema `notation_selection_contract.v1`)
* `.ananta/notation/allowed-notations.md` — human-readable summary

The worker prompt references these files; the full catalog is not
included inline, keeping the prompt short.

### Security Notes

| Risk | Mitigation |
|------|------------|
| Path traversal in output_filename | Renderer rejects `..` and absolute paths |
| Unknown element types | Renderer rejects with `NotationRenderError` before writing |
| Dangling references | Renderer rejects (e.g. flow sourceRef to unknown element) |
| Lane assignment errors | Renderer rejects unbalanced or duplicate assignments |
| LLM-invented diagram types | `PatternProposalNormalizer` strips unknown keys; only registry IDs are valid |
| Risk classification | Notation patterns are pure generators, classified as `low` risk — no opt-in required |
