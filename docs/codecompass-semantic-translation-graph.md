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

### Python symbol identity

Python graph identities are file- and occurrence-scoped. The v2 identity
strategy derives a digest from the separator-normalized, repository-relative
Git path, symbol kind, qualified symbol name, and positive declaration start
line and column. Git-path Unicode codepoints and permitted whitespace remain
identity-bearing: two distinct repository entries are never merged merely
because their display spelling is canonically equivalent. The readable prefix
retains the kind and an escaped symbol excerpt, for example:

```text
semantic:python:symbol:v2:function:main:<sha256>
```

The escaped excerpt is bounded; the digest still covers the complete symbol.
Content hashes, parser versions, index revisions, and manifest IDs remain node
or lifecycle evidence rather than identity inputs. Moving a declaration to a
different source position intentionally creates a new occurrence identity.
Consequently, two equal class or method names declared twice in one file cannot
collapse, and identical names in different files remain distinct as well.
Legacy IDs remain readable because graph consumers treat node IDs as opaque
strings. A saved legacy node ID or blast-radius seed is intentionally not
aliased to the replacement v2 ID after re-indexing; clients must select the node
from the new revision.

The identity factories retain a first-line/first-column default for older
direct callers. Language adapters always supply exact provenance. An injected
pre-v2 identity port is accepted through a compatibility adapter: its legacy
result remains an identity seed and is then scoped by the v2 occurrence, so
existing extensions neither fail on the changed call signature nor merge
repeated declarations.

### Other language symbol identities

TypeScript, Java, regex-fallback, and static-symbol occurrences follow the same
provenance rule through the shared
`repo_relative_file_provenance_canonical_symbol_sha256.v2` identity strategy.
Their local node ID covers the repository-relative path, language, symbol kind,
canonical semantic identity, local qualifier, and declaration start line and
column. This prevents equal names both across files and at multiple locations in
one file from collapsing into one supplement node. The language-level identity
remains available as
`attributes.canonical_semantic_id` for grouping and search; it is metadata,
not the occurrence identity. Edges emitted within an adapter run are rebound
to the exact local IDs whenever their endpoint is an unambiguous declaration
in that file. Ambiguous name-only relations are not guessed. Java discovery is
extension-bound, so Java-looking examples in Markdown, JSON, or shell scripts
are not mislabeled as Java symbols.

### Domain-fair worker materialization

Repository semantic output is grouped by normalized top-level path domain in
the delegated Worker. Two deliberately different read models are produced:

- The backward-compatible graph overview keeps independently bounded JSONL
  shards and a bounded 32 MiB graph artifact. Its deterministic aggregate
  admission remains useful for a fast project overview, but it is explicitly
  partial whenever its budget evidence reports omitted or truncated records.
- The revision-bound `cc_graph_domains.sqlite3` supplement retains every
  adapter-emitted semantic node, semantic relation, and file-declaration
  relation for every top-level domain. Streams are deterministic, compressed
  in independently verified chunks, and loaded only for the selected domain.
  A domain is published as complete or the whole supplement build fails; it is
  never silently published with missing records.

The supplement has a 128 MiB physical artifact safety envelope. That envelope
is an admission boundary, not a per-domain display quota: if the complete
supplement cannot be represented safely, the indexing run fails visibly
instead of selecting a preferred subset. Empty semantic domains are recorded
explicitly, so “complete with zero symbols” can be distinguished from a
missing or legacy supplement.

The private Worker-side SQLite source store has a separate disk envelope for
its temporary indexes and uncompressed records. It defaults to 2 GiB and can
be configured through
`ANANTA_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES` up to a 4 GiB hard
ceiling. This is a local resource guard, not a semantic node or per-domain
display limit: every admitted record must fit or the run fails without
activating a partial index.

Top-level directory identities are SHA-256 keys derived from their exact path
segment. Repository-root files use a namespace-tagged root identity rather
than the literal directory name `__repository_root__`; a real directory with
that name can therefore coexist without a collision. The supplement is bound
to the knowledge index, source-revision ID and digest, opaque source ID, graph
revision, logical content hash, and immutable artifact hash.

The shared output reader validates every overview shard as a contained, unique,
non-symlink relative path before reading it. It also retains the legacy
`semantic_nodes.jsonl` and `semantic_edges.jsonl` fallback, so existing indexes
remain consumable. Raw Worker path lists and normalized output-file evidence
round-trip through the same validation; hashes, counts, byte sizes, shard pairs,
and opaque domain hashes are rebound to the files on disk. Aggregate and
per-shard limits remain fail-closed, as does the final 32 MiB graph materializer;
omitted, unresolved, or truncated semantic evidence is reported as partial and
is never presented as complete. Separately, the Hub validates the immutable
SQLite schema, binding metadata, compressed and raw sizes, canonical JSONL,
chunk hashes, record counts, and logical hash before it can certify a selected
domain as complete. The Hub still owns task dispatch, artifact admission,
activation, domain selection, and graph reads; partitioning does not create a
Worker orchestration path.

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
