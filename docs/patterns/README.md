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
2. Adding template files under `config/patterns/templates/<language>/<pattern>/`
3. Running `python scripts/validate_pattern_catalog.py` to verify
4. Writing a gate checker in `agent/services/pattern_gate_service.py` (optional but recommended)

Pattern IDs follow `<language_prefix>.<pattern_name>` convention
(e.g. `java.strategy`, `python.function_stub`, `ts.vitest_scaffold`).
Language-agnostic patterns use a category prefix (e.g. `cli.retry_wrap`,
`workflow.sequential_emit`).
