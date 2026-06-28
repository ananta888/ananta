# CodeCompass Semantic Translation Vocabulary v1

Version: `v1`. Schema: `codecompass_semantic_translation_graph.v1`.

Unknown semantic kinds fail validation — they are never accepted silently.

---

## data_record

**Description:** An immutable or DTO-like data carrier. Models Java records, plain Java data classes, and similar value objects.

**Allowed attributes:** `name`, `kind` (record|class), `properties` (list), `methods` (list), `annotations` (list), `unsupported` (list)

**Java example:**
```java
public record UserDto(UUID id, String name) {}
```

**TypeScript example:**
```ts
export interface UserDto { id: string; name: string; }
```

**Kotlin example:**
```kt
data class UserDto(val id: String, val name: String)
```

**Known deviations:** Kotlin data class auto-generates `copy`, `equals`, `hashCode`, `toString` — Java record does the same but with different structural equality semantics than a plain class.

---

## property

**Description:** A named data field with a declared type and inferred nullability.

**Allowed attributes:** `name`, `type`, `order`, `annotations` (list), `nullability` (see Nullability Model), `warnings` (list), `line_start`

**Java example:**
```java
String name; // inside a class or record component
```

**TypeScript example:**
```ts
name: string;
```

**Kotlin example:**
```kt
val name: String
```

**Known deviations:** Unannotated Java fields produce `unknown_nullability`; Kotlin requires explicit `?` for nullable.

---

## enum_value

**Description:** A member of a closed enumeration.

**Allowed attributes:** `name`

**Java example:**
```java
enum Status { ACTIVE, DISABLED }
// → ACTIVE, DISABLED as enum_value nodes
```

**TypeScript example:**
```ts
export enum Status { ACTIVE = 'ACTIVE', DISABLED = 'DISABLED' }
```

**Kotlin example:**
```kt
enum class Status { ACTIVE, DISABLED }
```

**Known deviations:** TypeScript enum values are represented as string literals by default to match serialisation conventions.

---

## function_signature

**Description:** A method or function identified by name, parameters, return type, throws list and annotations.

**Allowed attributes:** `name`, `return_type`, `parameters` (list), `throws` (list), `throws_classified` (list), `visibility`, `static`, `final`, `annotations` (list), `side_effects` (list), `contracts` (preconditions/postconditions/invariants), `line_start`

**Java example:**
```java
public String find(UUID id) throws IOException;
```

**TypeScript example:**
```ts
// method signatures in interfaces require review — body semantics may differ
find(id: string): Promise<string>; // needs_review
```

**Kotlin example:**
```kt
fun find(id: String): String // may_throw IOException
```

**Known deviations:** Java checked exceptions have no direct equivalent in TypeScript; Kotlin uses unchecked exceptions only.

---

## nullable_value

**Description:** A value that may be `null` — explicitly declared via `@Nullable` or inferred.

**Allowed attributes:** (same as `property` with `nullability=nullable`)

**Java example:** `@Nullable String email`

**TypeScript example:** `email: string | null`

**Kotlin example:** `val email: String?`

**Known deviations:** TypeScript distinguishes `null` and `undefined`; Kotlin's `?` covers both Java `null` and `Optional.empty()` depending on policy.

---

## optional_absence

**Description:** An absence modelled with `java.util.Optional<T>` — not equivalent to `null`.

**Allowed attributes:** (same as `property` with `nullability=optional_absence`)

**Java example:** `Optional<String> email`

**TypeScript example:** `email?: string | undefined`

**Kotlin example:** `val email: String?` (only with `allow_optional_to_nullable` policy; otherwise `needs_review`)

**Known deviations:** `Optional.empty()` and `null` are semantically different in Java; mapping to Kotlin `?` conflates them unless the codebase is consistent.

---

## collection

**Description:** A finite ordered or unordered collection of typed elements.

**Allowed attributes:** `element_type`, `collection_kind` (list|set)

**Java example:** `List<String> tags`, `Set<UUID> ids`

**TypeScript example:** `tags: string[]`, `ids: string[]`

**Kotlin example:** `val tags: List<String>`, `val ids: Set<String>`

**Known deviations:** `Set` uniqueness is not enforced by TypeScript arrays — consumers must document this invariant explicitly.

---

## map

**Description:** A keyed collection mapping keys to values.

**Allowed attributes:** `key_type`, `value_type`

**Java example:** `Map<String, Long> counters`

**TypeScript example:** `counters: Record<string, number>`

**Kotlin example:** `val counters: Map<String, Long>`

**Known deviations:** TypeScript `Record` does not enforce key exhaustiveness; Kotlin `Map` is read-only by default.

---

## pure_expression

**Description:** An expression without observable side effects — can be evaluated repeatedly without changing state.

**Allowed attributes:** `expression`, `rule_id`

**Java example:** `a + b`, `a.equals(b)` (when receiver is non-null)

**TypeScript example:** `a + b`, `a === b`

**Kotlin example:** `a + b`, `a == b`

**Known deviations:** String concatenation semantics differ between Java (coercion to String) and TypeScript (number+string = string). Such cases are flagged as `needs_review`.

---

## side_effect

**Description:** A computation that reads from or writes to external state (DB, IO, network, time, random).

**Allowed attributes:** `effect_kind` (one of EFFECT_KINDS), `description`

**Java example:** Any method marked with `@Transactional` or containing `jdbc.*`, `Files.*`, `System.currentTimeMillis()`

**TypeScript / Kotlin equivalent:** No structural equivalent — side effect contracts must be documented manually or via annotations.

**Known deviations:** Java methods with unknown bodies are conservatively marked `unknown_side_effect`, never `pure`.

---

## exception_flow

**Description:** An exception that may be thrown or propagated from a method.

**Allowed attributes:** `throws` (list), `throws_classified` (list — each entry has `name` and `kind`: `checked_exception`, `unchecked_exception`, or `may_throw`)

**Java example:** `throws IOException` → `checked_exception`; `throws NullPointerException` → `unchecked_exception`

**TypeScript example:** No equivalent — exception flows produce `needs_review` or are modelled as returned error types.

**Kotlin example:** All exceptions are unchecked in Kotlin — `throws` is advisory only.

**Known deviations:** Checked exception contracts are lost in TypeScript and weakened in Kotlin. This is always flagged with a warning.
