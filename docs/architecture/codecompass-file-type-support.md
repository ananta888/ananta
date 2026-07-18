# CodeCompass file-type support contract

## Purpose and boundary

`config/codecompass/file_type_support.v1.json` is the canonical, declarative
inventory for file classification and observable parser support. Its schema is
`schemas/codecompass/file_type_support_registry.v1.json`; the container-neutral
Python contract lives in `ananta_contracts.file_type_support`.

The registry is **not an authorization policy**. `enabled` means that a format
participates in deterministic classification. It does not permit a Hub or
worker to read a path, bypass excludes, ingest secrets, execute code, render
active content, or invoke a parser. Consumers must apply their own path,
secret, size, resource, and authorization policy before opening a file.

This keeps the hub–worker boundary intact:

- the Hub may select policy and report declared/effective support;
- workers perform delegated parsing and emit bounded records;
- both sides consume the neutral contract rather than importing each other's
  concrete implementations;
- workers do not orchestrate other workers.

## Three independent support dimensions

Every format/pipeline pair reports three explicit dimensions:

1. `indexed`: bounded content or structured records can enter that pipeline;
2. `symbols`: addressable declarations, headings, keys, selectors, or similar
   symbols are emitted with provenance;
3. `relationships`: typed structural or semantic edges are emitted.

A relationship declaration requires symbol support, and symbol support
requires indexing. The reverse does not hold: indexing alone is not symbol
support, and Tree-sitter symbol extraction alone is not semantic relationship
analysis.

Each dimension exposes separate facts:

- `configured`: an implementation and producer are declared;
- `runtime_available`: all declared optional runtime requirements were probed;
- `verified`: repository evidence names a test-backed declaration;
- `effective`: configured, verified, and currently runtime-available.

`effective_level` in the exported matrix is a derived display value only. The
three capability records remain the source of truth.

Implementation grades are intentionally modest:

- `unsupported`: no implementation claim;
- `text_fallback`: content-only bounded indexing;
- `heuristic`: deterministic but incomplete structural extraction;
- `parser`: parser-backed extraction.

Parser-backed is not synonymous with verified or semantic. Python semantic
translation is AST-backed. Java uses a smoke-tested Tree-sitter grammar when
available and an explicitly reduced-confidence regex fallback. TypeScript,
TSX, JavaScript and JSX use a non-executing structural adapter. Go, Rust, C,
C++, C#, Ruby and PHP deliberately claim only heuristic symbol indexing in
semantic translation: their import graph records do not promote the whole
adapter to semantic relationship support. Repository-map languages likewise
do not claim relationships merely because an optional Tree-sitter parser can
find declarations.

## Implemented support lanes

Registry version `1.1.0` records the following independently testable lanes:

- `setup_index` performs bounded, redacted text indexing for every active
  descriptor. It does not claim symbols or relationships.
- `rag_helper` emits structured records for Markdown, MDX, reStructuredText,
  AsciiDoc, YAML, TOML, INI-style configuration, properties, Angular HTML,
  CSS/SCSS/Sass/Less, Dockerfile/Containerfile variants, Compose, GitHub Actions, Makefiles,
  Jenkinsfiles, shell, PowerShell, XML/XSD, SQL, JSON Schema, Proto, GraphQL,
  Terraform, CSV/TSV, notebooks, Mermaid, PlantUML, Graphviz DOT and draw.io.
  CSV/TSV stops at symbol/schema records; it does not advertise relationships.
- `semantic_translation` provides verified relationships for Python, Java and
  TypeScript/JavaScript-family files. The Go/Rust/C/C++/C#/Ruby/PHP fallbacks
  advertise only the lower `symbol_index` level. Kotlin/KTS, Swift, Scala,
  Lua, Dart, Vue and Svelte use inert static symbol adapters with deliberately
  reduced confidence and no relationship claim. Vue/Svelte only inspect a
  closed script block; template and style content is never executed.
- `repository_map` reports Tree-sitter requirements separately from verified
  regex fallbacks. A grammar package being installed is not enough: runtime
  parser resolution performs a parse smoke probe before advertising it.

The registry also classifies `.fish`, `.tsv`, `.drawio`, `.mmd`, `.mermaid`,
`.puml`, `.plantuml`, `.dot`, `.gv` and `.adoc`. JSON Schema uses the more
specific `*.schema.json`/`.schema.json` selectors so generic `.json` files are
not falsely advertised as schema-parsed. PowerShell data manifests (`.psd1`)
are deliberately limited to bounded setup text indexing; only `.ps1` and
`.psm1` use the static PowerShell domain extractor.

Structured extraction is static. It never renders HTML or diagrams, executes
scripts, invokes Docker/Terraform/protoc, runs notebook cells, follows document
includes, connects to a database, or evaluates template/config expressions.
XML and draw.io parsing deny DTD/entity/network access and enforce byte, node,
depth and attribute limits. XInclude elements remain inert XML nodes and are
never dereferenced. Configuration values matching secret-bearing keys are
omitted rather than copied into symbol records.

## Deterministic classification

`ananta_contracts.file_type_classifier.FileTypeClassifier` applies this fixed
precedence:

1. exact filename;
2. filename pattern;
3. compound suffix;
4. ordinary extension;
5. shebang on a bounded first line;
6. explicit text fallback.

This lets `.github/workflows/ci.yml` classify as GitHub Actions rather than
generic YAML, `docker-compose.dev.yml` as Compose, and
`variables.tfvars.json` as Terraform rather than JSON. Diagram extensions,
tabular `.tsv`, Fish scripts and draw.io files are ordinary explicit selectors;
they do not depend on the text fallback. Within one stage,
explicit `match_priority`, selector specificity, format id, and selector form
a stable ordering. Duplicate active literal selectors are rejected unless all
owners supply distinct priorities.

Classification performs no I/O. A consumer supplies path metadata, an
optional bounded first line, and whether its own binary probe considers the
file text. This makes policy and file access explicit and testable.

## Source-grounded audit

Run the read-only audit from the repository root:

```bash
python scripts/audit_codecompass_file_type_support.py
python scripts/audit_codecompass_file_type_support.py --output json
python scripts/audit_codecompass_file_type_support.py --strict
```

The audit uses `git ls-files`, reads at most 4096 bytes per regular tracked
file, validates every evidence path, probes declared Python modules,
executables and Tree-sitter languages, reports classifier coverage, and
exports the full capability matrix. It never writes an index and never
executes file content.

Runtime requirements distinguish `python-module:*`, `executable:*`, and
`tree-sitter-language:*`. The last form resolves the configured language and
performs a real parse smoke test; it must be used for grammar capabilities
because a package import alone does not prove parser compatibility.

Default mode fails for an invalid registry or missing evidence. `--strict`
also fails when an optional declared runtime requirement is unavailable. This
distinguishes missing dependencies from false semantic-support claims.
For example, a container with Java and C# grammar wheels but without the
Go/Rust/C/C++/Ruby/PHP wheels reports those repository-map parser capabilities
as configured but unavailable; the independent semantic symbol fallbacks stay
effective.

## Hub API and operator CLI

The authenticated Hub endpoint is read-only:

```text
GET /knowledge/file-type-support
```

It accepts repeatable `priority`, `support_level` (or `level`), `dimension`
and `pipeline` filters. `missing_parser`, `missing_runtime`, and `enabled` are
strict booleans. Unknown query fields and unknown enum values fail with a 400
response. The response keeps extensions, exact filenames, compound suffixes,
patterns and shebang selectors separate and includes the registry digest.

`runtime_scope=hub_process` is intentionally not presented as worker truth.
The Hub can report the dependencies installed in its own container; the
executing worker must publish its observed availability in the run manifest.
This avoids implicit shared-state assumptions across containers.

The audit command exposes the same filters, for example:

```bash
python scripts/audit_codecompass_file_type_support.py \
  --priority P0 --pipeline rag_helper --dimension relationships
python scripts/audit_codecompass_file_type_support.py \
  --missing-parser --output json
```

## Coverage and run evidence

`scripts/setup_codecompass_index.py --coverage-json <path>` produces a
deterministically ordered per-file report. Every discovered path has one of
`indexed`, `excluded`, `unsupported`, or `failed`; exclusions require an
explicit reason. Aggregates include byte size and counts/shares by detected
type, support level, parser strategy, outcome, exclusion reason and diagnostic.
Unknown text and binary/unclassified content remain distinct.

The existing CodeCompass `manifest.json` is enriched additively; no competing
second manifest is introduced. It carries:

- registry schema/version and SHA-256 snapshot hash;
- complete candidate accounting (`indexed + excluded + unsupported + failed`);
- observed capability claims per type and pipeline;
- parser id/version, fallback reason and bounded diagnostics.

The worker-side reader rejects contradictory claims: effective symbols require
effective indexing, effective relationships require effective symbols, and an
effective mode requires configured, runtime-available and verified support.
Negative or inconsistent counts and duplicate type/pipeline claims fail
closed.

## Rollout and resource controls

The setup index supports a staged rollout without editing code:

```bash
ANANTA_CODECOMPASS_FILE_TYPE_PRIORITIES=P0,P1 \
ANANTA_CODECOMPASS_DISABLED_FORMATS=notebook,drawio \
python scripts/setup_codecompass_index.py --dry-run
```

`ANANTA_CODECOMPASS_ENABLED_FORMATS` changes the selection to an explicit
allow-set; `ANANTA_CODECOMPASS_DISABLED_FORMATS` is a deny-set. The two sets
must not overlap and unknown format ids are rejected. Equivalent repeatable
`--enable-format` and `--disable-format` options are available. Candidate
selection is deterministic and round-robins format families inside each
priority so a large code directory cannot silently displace all documentation
or configuration.

Shared `ANANTA_CODECOMPASS_*` settings are the Hub-owned ceilings for input
bytes, lines, cooperative parser wall time, output records, XML nodes/depth,
YAML aliases, notebook cells/cell text/outputs and CSV rows/columns. Semantic
translation and RepositoryMap consume the neutral `ParserLimits` projection.
For Hub-triggered Rag-helper indexing, `RagHelperIndexService` narrows an
injected `ProcessingLimits` instance with the same settings; profile values
may be stricter but cannot widen the Hub policy. The setup index additionally
retains its deliberately stricter 48,000-byte per-record cap to prevent a
2,000-record batch from becoming an unbounded HTTP payload. Limit, timeout and
security violations have distinct diagnostic codes.

The timeout is an honest cooperative wall-time guard: adapters run without
process-global mutation, elapsed time is checked after their return, and late
output is discarded with `parser_timeout`. It is not advertised as hard
preemption. Parsers that need hard isolation must remain in a delegated
container/process with its own external execution deadline.

The standalone Rag-helper container receives corresponding explicit CLI or
profile values (`--max-file-size-kb`, `--max-parser-lines`, XML/YAML, notebook,
tabular and draw.io limits). `ProcessingLimits` is injected into extractors;
there is no process-global mutable parser policy. Container boundaries remain
explicit: a standalone worker does not inherit the Hub environment and must
receive its delegated policy through configuration.

Feature activation never bypasses path policy. Symlinks, traversal, secret
paths, binary content and oversized inputs remain excluded before parsing.

## Targeted migration and metrics

`ananta_contracts.file_type_migration.compare_file_type_registries` compares
canonical descriptor hashes and returns only added, removed or changed format
ids. `affected_paths` classifies each candidate against the old and new
registry and retains unrelated paths as cacheable. The Hub-owned repository
index flow applies the same rule operationally: it persists descriptor hashes,
effective format ids, a content-sensitive source fingerprint and per-file
detected types; a stable sharded incremental cache invalidates only paths whose
classification or descriptor changed. Missing legacy evidence fails safely to
a full invalidation of known manifest paths. The Hub remains responsible for
scheduling re-index work; workers neither compare nor coordinate each other.
Registry version/digest and parser id/version remain persisted with index/run
evidence so an operator can prove which generation produced an artifact.

Prometheus observations use bounded registry-derived labels and cover file
outcomes, fallbacks, diagnostics, duration, bytes, symbol count and edge count
for setup indexing, Rag-helper, RepositoryMap and direct semantic translation.
The setup text lane reports aggregate per-type processing duration and explicit
zero symbol/edge counts; it does not invent structure that it never parsed.
Unknown labels collapse to `other`, preventing repository-controlled
metric-cardinality growth. Metrics are emitted through an injectable lazy port
and remain observational: failure to record them never changes a successfully
persisted index result.

Recommended deployment order is P0, then P1, then P2. Before enabling another
priority, run the non-strict audit, the relevant parser golden tests and a
coverage dry run. Strict audit mode is a deployment gate only when every
optional grammar required by that image is intentionally installed.

## Adding or promoting support

1. Add selectors without colliding with an existing active mapping.
2. Keep classification separate from pipeline integration.
3. Implement a focused worker-side parser or consumer adapter.
4. Add bounded fixtures for content, symbols, relationships, malformed input,
   size limits, and active-content non-execution.
5. Declare only the dimensions that the producer actually emits.
6. Add repository-relative production and test evidence.
7. Run the contract tests and audit before promoting `verified`.

Do not promote `relationships` for a parser that only emits containment-free
symbol names. Do not set `verified` merely because a dependency imports. A
real fixture must exercise the claimed output, provenance, and failure mode.
