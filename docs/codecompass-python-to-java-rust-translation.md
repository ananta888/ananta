# CodeCompass: Python → Java / Rust Translation

Version: `v1`. Track: `codecompass-python-to-java-rust-translation`.

This document describes the supported Python subset, hard limits, type mapping policy, and how the translation pipeline works.

**Important:** This is not a universal migration tool. Untypised, highly dynamic Python code cannot be automatically transformed. See [Hard Limits](#hard-limits).

---

## Supported Python Subset

The following Python constructs can be analysed and transformed deterministically:

| Construct | Java Target | Rust Target |
|---|---|---|
| `@dataclass` | `record` or `class` | `struct` |
| `@dataclass(frozen=True)` | `record` | `struct` with `PartialEq` |
| `class X(Enum)` | `enum` | `enum` |
| `class X(TypedDict)` | `record` / DTO | `struct` |
| Annotated functions | method signatures | `fn` signatures |
| `list[T]` | `List<T>` | `Vec<T>` |
| `set[T]` | `Set<T>` | `HashSet<T>` |
| `dict[K,V]` | `Map<K,V>` | `HashMap<K,V>` |
| `Optional[T]` / `T \| None` | `Optional<T>` | `Option<T>` |
| `bool` | `boolean` | `bool` |
| `int` | `long` (policy) | `i64` (policy) |
| `float` | `double` | `f64` |
| `str` | `String` | `String` / `&str` (param) |
| `bytes` | `byte[]` | `Vec<u8>` |
| `Decimal` | `BigDecimal` | `rust_decimal::Decimal` |
| `datetime` | `LocalDateTime` | `chrono::DateTime<Utc>` |
| `date` | `LocalDate` | `chrono::NaiveDate` |
| `UUID` | `java.util.UUID` | `uuid::Uuid` |
| `None` (return) | `void` | `()` |

---

## Type Confidence Levels

Every parameter and field receives a confidence level:

| Level | Meaning | Auto-transform allowed? |
|---|---|---|
| `annotated` | Explicit type annotation present | Yes |
| `inferred_from_default` | Type inferred from literal default | Yes (approved cases) |
| `inferred_local` | Type inferred from local assignment | Review required |
| `unknown` | No annotation, cannot infer | No — needs_review |
| `dynamic` | `Any`, `eval`, metaclass, etc. | No — blocked |

Automatic transformation is only allowed for `annotated` and approved `inferred_from_default` cases.

---

## None / Optional Semantics

Python has multiple forms of "no value":

| Python form | Model | Java | Rust |
|---|---|---|---|
| `None` | `none_literal` | `void` (return), `@Nullable` (field) | `()` or `Option<T>` |
| `Optional[T]` | `optional_type` | `Optional<T>` | `Option<T>` |
| `T \| None` | `optional_type` | `Optional<T>` | `Option<T>` |
| `= None` default | `default_none` | `Optional<T>` (warning) | `Option<T>` |
| `0`, `""`, `[]` | `falsy_empty` | not mapped to null | not mapped to None |

**Falsy values like `0`, `""`, `[]` are never mapped to null/None.** This is a hard constraint.

---

## Numeric Precision Policy

Python `int` is arbitrary-precision. The default mapping is:

- Java: `long` — covers 64-bit integers. Use `BigInteger` policy for unbounded values.
- Rust: `i64` — covers 64-bit integers. Use `i128` or `num_bigint::BigInt` policy for large values.

Every `int` mapping produces a `int_precision_policy` warning unless explicitly configured otherwise. This warning does not block transformation.

---

## Rust Ownership Policy (v1)

In v1, the engine uses a conservative ownership model:

- All struct fields are **owned** by default.
- Function string parameters use `&str` (more ergonomic than `String`).
- Function `Vec<T>` parameters use `&[T]`.
- Mutable Python objects produce warnings — they do not automatically become `Arc<Mutex<T>>`.
- Complex reference cycles → `lifetime_unknown` → `needs_review`.
- No Rust lifetime annotations are emitted automatically in v1.

Every ownership decision is recorded in the transform artifact under `ownership_decisions`.

---

## Dynamic Feature Blockers

The following Python features block automatic transformation entirely:

| Feature | Blocker code | Severity |
|---|---|---|
| `eval()` | `eval_usage` | blocker |
| `exec()` | `exec_usage` | blocker |
| `__import__()` | `dynamic_import` | blocker |
| `importlib.import_module()` | `dynamic_import` | blocker |
| `getattr(obj, dynamic_name)` | `dynamic_attribute_access` | blocker |
| Custom metaclass | `custom_metaclass` | blocker |
| `from x import *` | `star_import` | warning |
| Monkey patching | `monkey_patching` | warning |

A blocked Python file produces a `blocked_dynamic_runtime` status in the translation plan. No target code is emitted.

---

## Examples

### Dataclass → Java Record

```python
@dataclass
class User:
    name: str
    age: int
    email: Optional[str] = None
```

Emits:

```java
import java.util.Optional;

public record User(
    String name,
    long age,
    Optional<String> email
) {}
```

Warnings: `int_precision_policy: age — Python int mapped to long`

### Dataclass → Rust Struct

```python
@dataclass(frozen=True)
class Point:
    x: float
    y: float
```

Emits:

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}
```

### Python Enum → Java / Rust

```python
class Status(Enum):
    ACTIVE = 1
    DISABLED = 2
```

Java:
```java
public enum Status {
    ACTIVE,
    DISABLED
}
```

Rust:
```rust
#[derive(Debug, Clone, PartialEq)]
pub enum Status {
    ACTIVE,
    DISABLED,
}
```

---

## Hard Limits

The following are **not supported** in v1 and will not be supported automatically:

- Metaclasses, `__new__`, `__class_getitem__`, `__init_subclass__`
- `eval`, `exec`, `__import__`, `importlib.import_module`
- Monkey patching (assigning to class attributes after class definition)
- Multiple inheritance with complex MRO
- Lambdas or nested functions as class fields
- Framework code (Django models, FastAPI routes, SQLAlchemy)
- Automatic Rust lifetime annotation inference
- `*args` / `**kwargs` without explicit policy

Attempting to transform code with these patterns produces a `blocked_dynamic_runtime` or `needs_review` status — **never silent wrong output**.

---

## Feature Flag

The entire translation stack is controlled by:

```
ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED=true
```

When `false` (default), no Python analysis runs and existing CodeCompass behaviour is unchanged.
