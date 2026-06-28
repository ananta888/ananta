# CodeCompass x86 Assembly Core Extension — Contract

**Status:** foundation contract for `codecompass-x86-assembly-core-extension`
**Owner:** Ananta CodeCompass / Hub
**Related todos:**
- `todos/todo.codecompass-x86-assembly-core-extension.json` (this track)
- `todos/todo.codecompass-x86-malware-c-analysis-lifting.json` (depends on this contract)

---

## Scope

This track extends CodeCompass so that x86/x86-64 assembly, disassembly
output, binary metadata, addresses, sections, basic blocks, control-flow,
register/flag/stack/memory semantics, and analysis artifacts can be indexed,
searched, traversed, and handed off to workers/agents as **first-class
code context** (not as a second-class text file).

The Core extension is **deliberately separate** from:

- Decompilation to C / lifting (follow-up tracks).
- Malware-specific behavior analysis (depends on Core but is a separate track).
- Sandbox execution, unpacking, or sample activation (out of scope, see Non-Goals).

The Core's job is **belastbare x86-Kontextrepräsentation**: nodes, edges,
location refs, query API, evidence, trace, tests, documentation.

## Non-Goals

The Core extension explicitly does **not** include:

- Direct decompilation to C.
- Malware-specific behavior analysis (covered by the malware track).
- **Execution** of unknown binaries. Static-only.
- Unpacking, decryption, or activation of unknown samples.
- Direct translation to other high-level languages.
- LLM-based semantic invention.
- Optimization, obfuscation, or modification of assembly code.
- Replacement for specialized disassemblers/lifters; CodeCompass normalizes and links their results.

## Architecture

x86 / assembly is a **first-class source** for CodeCompass, not a text file
of lower trust. Three layers:

1. **Schema layer** (`agent/codecompass/x86/models.py`) — Node kinds, edge types, address model, location refs, provenance, evidence kinds, confidence model.
2. **Adapter layer** (`agent/codecompass/x86/adapter.py`, `fixture_adapter.py`, `capstone_adapter.py`) — pluggable disassembler adapters. Fixture adapter for tests; Capstone adapter prepared but not required at runtime.
3. **Indexing layer** (`agent/codecompass/x86/index_builder.py`, `index_pipeline.py`) — produces bounded, traceable index records.

Indexing is **static and bounded**. It does not execute samples, does not
contact network endpoints derived from binaries, and does not write
extracted payloads without explicit downstream approval.

## Node Schema

x86-specific node kinds (see `X86NodeKind` in `models.py`):
`binary_file`, `section`, `segment`, `symbol`, `function`, `basic_block`,
`instruction`, `operand`, `register`, `flag`, `stack_slot`,
`memory_region`, `callsite`, `import`, `export`, `relocation`,
`string_literal`, `address_range`.

Each node carries a stable id, kind, source_type, path, address/offset
when applicable, architecture_profile, confidence, and provenance.

## Edge Schema

x86-specific edge types (see `X86EdgeType` in `models.py`):
`contains`, `belongs_to`, `next_instruction`, `cfg_fallthrough`,
`cfg_true`, `cfg_false`, `cfg_jump`, `cfg_indirect_jump`, `calls`,
`indirect_calls`, `returns_to`, `reads_register`, `writes_register`,
`reads_flag`, `writes_flag`, `reads_memory`, `writes_memory`,
`uses_stack_slot`, `references_string`, `references_import`,
`references_address`, `relocates_to`.

## LocationRef

Location refs support virtual address, file offset, section, symbol,
function, basic block, and instruction index. Address refs distinguish
`absolute_address`, `relative_address`, `file_offset`, `rva`, and
`unknown`. Unresolved addresses produce `address_unresolved` — never
fabricated paths.

## Safety Policy

Binary and assembly inputs are **always untrusted**:

- Core indexing never executes unknown binaries.
- Core indexing never contacts external addresses, domains, URLs, or services derived from analyzed data.
- Core indexing never writes extracted payloads, dumps, or transformed binaries without explicit downstream tool approval.
- The policy distinguishes read-only parsing, static metadata extraction, fixture-based tests, and explicitly approved sandbox usage.
- For this Core track, **sandbox execution is out of scope and technically disabled.**

The malware track inherits and tightens this policy further.

## Feature Flags

The master switch is:

- `ANANTA_CODECOMPASS_X86_ENABLED` (default: `false`).

Sub-flags (all default `true` when the master is on):

- `ANANTA_CODECOMPASS_X86_RAW_ASSEMBLY`
- `ANANTA_CODECOMPASS_X86_BINARY_METADATA`
- `ANANTA_CODECOMPASS_X86_DISASSEMBLER_EXPORT`
- `ANANTA_CODECOMPASS_X86_CFG`

Other:

- `ANANTA_CODECOMPASS_X86_EXPERIMENTAL_ADAPTER` (default `false`).
- `ANANTA_CODECOMPASS_X86_DEFAULT_PROFILE` (default `x86_64_sysv`).

Limits (default-off means the defaults only apply when enabled):

- `ANANTA_CODECOMPASS_X86_MAX_INSTRUCTIONS` (50 000)
- `ANANTA_CODECOMPASS_X86_MAX_FUNCTIONS` (5 000)
- `ANANTA_CODECOMPASS_X86_MAX_BASIC_BLOCKS` (20 000)
- `ANANTA_CODECOMPASS_X86_MAX_STRINGS` (10 000)

Profiles: `x86_64_sysv`, `x86_64_windows`, `x86_32_cdecl`,
`x86_32_stdcall`, `unknown_x86`. Invalid profiles raise clear diagnostics
and never silently fall back to a wrong default.

## Indexing Flow

1. Input comes in as an `X86InputRecord` (kind, architecture, bitness, syntax, ABI, source path).
2. The pipeline selects an `X86DisassemblerAdapter` based on input kind and profile.
3. The adapter produces instruction records + diagnostics.
4. `X86IndexBuilder` produces bounded index records.
5. The pipeline emits diagnostics and respects limits; truncation produces visible warnings, not silent loss.

## Tooling

The Core extension is consumed via the existing CodeCompass tool surface.
The Core extension itself does **not** introduce worker tools; the tools
listed in milestone M5 belong to `agent.services.tools.codecompass_tools`
and reuse the schema/builder modules defined here.

## Relationship to the Malware Track

The malware analysis track **depends on this Core contract** for:

- The node schema (functions, basic blocks, instructions, operands, registers, flags, stack slots).
- The edge schema (CFG, calls, reads/writes).
- The location ref model.
- The provenance/confidence model.
- The safety policy (with additional malware-specific tightening).

The malware track does **not** redefine Core fields; it extends them
through composition (Behavior Graph, IOC records, IR functions, C
pseudocode view). Any conflict between this contract and the malware
contract resolves in favor of **this Core contract**.

## Acceptance Criteria

This contract document is itself an acceptance deliverable for X86CC-001
(see `todos/todo.codecompass-x86-assembly-core-extension.json`). The
deliverable is verified by `tests/codecompass/x86/test_doc_contract.py`.

## Tools (W5 / X86CC-023..027)

The x86 extension exposes five tools through `agent.services.tools.codecompass_tools`,
following the same `workspace_dir / arguments / tool_call_id -> ToolResult` shape
as the existing CodeCompass tools:

- `codecompass.x86_overview` — counts, kinds present, section breakdown for the indexed x86 payload.
- `codecompass.x86_address_lookup` — resolve a single hex (`0x...`) or decimal address to matching nodes.
- `codecompass.x86_cfg` — bounded, cycle-safe CFG traversal from a seed node id.
- `codecompass.x86_call_graph` — classify calls from a seed function into direct, indirect, import, unresolved.
- `codecompass.x86_find` — generic finder for the 9 query kinds: `address`, `symbol`, `function`, `mnemonic`, `import`, `string`, `section`, `basic_block`, `diagnostics`.

All five tools degrade to `status: ok` with empty data when no x86 records
are present in the persisted graph. They never raise — invalid arguments
return `status: error` with a human-readable reason.

## Sample Queries (W6 / X86CC-032)

Workers and operators can copy-paste these argument dicts into the
corresponding tools. All return JSON-serializable ToolResult payloads.

```python
# 1) summarize the indexed x86 payload
codecompass.x86_overview(arguments={})

# 2) resolve an address (hex or decimal)
codecompass.x86_address_lookup(arguments={"address": "0x401014"})

# 3) walk the CFG from a seed
codecompass.x86_cfg(arguments={"seed_id": "fn-abc123", "max_depth": 20, "max_nodes": 200})

# 4) classify the call graph from a seed function
codecompass.x86_call_graph(arguments={"seed_id": "fn-abc123"})

# 5) find by mnemonic (limit)
codecompass.x86_find(arguments={"kind": "mnemonic", "value": "mov", "limit": 50})
codecompass.x86_find(arguments={"kind": "function", "value": "add_two"})
codecompass.x86_find(arguments={"kind": "import", "value": "printf"})
codecompass.x86_find(arguments={"kind": "string", "value": "http"})
```

## Worker Context Handoff (W6 / X86CC-028)

`agent.codecompass.x86.handoff.build_x86_worker_context(nodes=..., edges=..., function_id=...)`
produces a `codecompass_x86_context.v1` package for one function: the function
node itself, the instructions that belong to it (by address range), any calls
it makes, and warnings (e.g. `function_end_address_missing`,
`indirect_calls_present`). Used by worker tools that need a self-contained
view of a function without re-walking the graph.