# CodeCompass Semantic Translation Graph

This track adds a deterministic semantic translation layer to CodeCompass. It does not replace retrieval and it is not a universal compiler.

## Motivation

Text-to-text code translation loses contracts, nullability, side effects and type semantics too easily. The Semantic Translation Graph models source code as structured nodes and edges first, then applies versioned equivalence rules and deterministic transforms.

## Architecture

Schema: `codecompass_semantic_translation_graph.v1`.

Core record kinds:
- Nodes: `syntax_node`, `semantic_node`, `type_node`, `symbol_node`, `control_flow_node`, `data_flow_node`, `effect_node`, `contract_node`, `equivalence_rule`, `transform_artifact`
- Edges: `declares`, `uses`, `calls`, `reads`, `writes`, `returns`, `throws`, `maps_to`, `equivalent_to`, `requires`, `ensures`, `generated_by`, `verified_by`
- Provenance: file, language, symbol, line range, parser/adapter, confidence and creation time

Default feature flag: `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED=false`.

## Scope V1

Supported first-scope source constructs:
- Java records
- Java DTO-style classes
- Java enums
- Java interfaces and method signatures as reviewable contracts
- Primitive/String/UUID/BigDecimal/LocalDate/LocalDateTime/List/Set/Map/Optional type mappings

Supported target constructs:
- TypeScript `interface`, `enum`, optional properties and union absence
- Kotlin `data class`, `enum class`, nullable type markers under policy

## Semantic Vocabulary

Version `v1` concepts:
- `data_record`: immutable or DTO-like data carrier
- `property`: named data field with type and nullability
- `enum_value`: closed set value
- `function_signature`: name, params, return type, throws and annotations
- `nullable_value`: value may be null
- `optional_absence`: Java Optional-style absence, not equivalent to null
- `collection`: finite collection
- `map`: keyed collection
- `pure_expression`: expression without side effects
- `side_effect`: IO, database, network, time or random access
- `exception_flow`: thrown or propagated exception

Unknown semantic kinds fail validation instead of being accepted silently.

## Workflow

1. Adapter emits semantic graph records.
2. Registry resolves type and equivalence rules.
3. Transform engine generates target model/code only when preconditions are satisfied.
4. Verifier checks output properties, enum values, type mappings and warnings.
5. Artifact records source hash, target hash, rule IDs, warnings, verifier status and timestamp.

LLMs may propose rules, but new rules start as experimental. Promotion to stable requires schema validation, golden tests, examples, no high-risk warnings and explicit review.

## Tutorial

Java record:

```java
public record UserDto(UUID id, String name, Optional<String> email) {}
```

TypeScript:

```ts
export interface UserDto {
  id: string;
  name: string;
  email?: string | undefined;
}
```

Kotlin requires explicit Optional-to-nullable policy:

```kt
data class UserDto(
    val id: String,
    val name: String,
    val email: String?
)
```

## Known Limits

Nullability without annotations becomes `unknown_nullability`. BigDecimal to TypeScript number is lossy unless policy explicitly accepts it. Framework magic, reflection, runtime proxies, complex method bodies, checked exception behavior and side-effect semantics often require review.

