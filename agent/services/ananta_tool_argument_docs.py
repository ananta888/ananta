"""What each argument of the worker tools means, and which ones a call needs.

The registry specs in ``ananta_tool_registry_service`` name arguments and
types only; models (and the decision fast path, which reads these texts) had
to guess what ``mode``, ``profile`` or ``question`` mean. The values and
limits here are the ones the implementations in ``agent/services/tools``
enforce. ``REQUIRED`` lists arguments a call fails without; tools that need
one of several arguments say so in the description instead.
"""

from __future__ import annotations

ARGUMENT_DOCS: dict[str, dict[str, str]] = {
    "repo.list_files": {
        "path_glob": "fnmatch pattern on the workspace-relative path, e.g. 'agent/**/*.py'; empty lists all files.",
        "limit": "Maximum files, default 200, at most 500.",
    },
    "repo.read_file_range": {
        "path": "Workspace-relative file path.",
        "line_start": "First line (1-based), default 1.",
        "line_end": "Last line, default line_start + 80; at most 400 lines per call.",
    },
    "repo.grep": {
        "pattern": "Python regular expression, e.g. 'class CircuitBreaker'.",
        "path_globs": "List of fnmatch patterns (any may match), e.g. ['agent/**/*.py'].",
        "limit": "Maximum matches, default 50, at most 100.",
        "context_before": "Lines of context before each match, 0-20.",
        "context_after": "Lines of context after each match, 0-20.",
    },
    "codecompass.plan_context": {
        "query": "What the code change or question is about.",
        "intent": "Editor planning mode; when set, registry_version, node_kind and backend_contract or symbols are "
                  "required too.",
        "detail_level": "'preview' (metadata only), 'selected' or 'conversation' (default).",
        "registry_version": "Required with intent: version of the node registry.",
        "node_kind": "Required with intent: kind of the selected node.",
        "field_path": "Optional field path inside the node.",
        "backend_contract": "With intent: the backend contract (string or object); or give symbols instead.",
        "symbols": "Symbol names to anchor the plan (at most 32).",
        "graph_neighbors": "Neighbor node ids to include (at most 32).",
        "max_ranges": "Maximum location ranges, default 8, at most 40.",
        "include_neighbors": "Include graph neighbors, default true.",
        "task_kind": "Free label, echoed back.",
    },
    "codecompass.resolve_context": {
        "query": "The task or question the context is for.",
        "task_kind": "Free label, e.g. 'bugfix', 'review'; shapes the context hints.",
        "mode": "'compact' (about 4k tokens), 'balanced' (default, 12k) or 'deep' (32k; needs working_files or "
                "domain_scope).",
        "working_files": "Workspace paths to put first.",
        "domain_hint": "Topic that biases the search and the domain map.",
        "domain_scope": "Domain the context is limited to; also allows mode 'deep'.",
        "max_tokens": "Token budget, reported only.",
        "max_files": "Maximum candidate files, 1-20.",
        "include_original_files": "Also read up to 8 original files, default false.",
        "include_jsonl_records": "Blocked by policy.",
        "include_graph": "Add graph edges from the top candidates, default false.",
        "llm_scope": "'local' (default); values starting with 'external' return metadata only.",
    },
    "codecompass.search": {
        "query": "What to look for in the project index (code, docs, symbols).",
        "limit": "Maximum hits, default 8, at most 20.",
        "mode": "'auto' (default), 'hybrid', 'vector', 'exact' (literal names) or 'graph'.",
        "requested_signals": "Subset of ['exact', 'graph', 'vector'] to narrow the plan.",
        "task_kind": "'architecture_question' changes the plan; other values are labels.",
        "revision": "Index revision; may only narrow the authorized scope.",
        "allowed_paths": "Path prefixes to search in; may only narrow the authorized scope.",
        "max_chars": "Answer size, default 8000, 256-32000.",
    },
    "codecompass.retrieve": {
        "query": "What to retrieve (at most 4000 characters).",
        "mode": "'auto' (default), 'hybrid', 'vector', 'exact' or 'graph'.",
        "requested_signals": "Subset of ['exact', 'graph', 'vector'] to narrow the plan.",
        "task_kind": "'architecture_question' changes the plan; other values are labels.",
        "scope": "{repository_id, revision, allowed_paths[<=64]}; may only narrow the authorized scope.",
        "budget": "{top_k (8, 1-20), max_chars (8000), max_tokens (3000), candidate_limit (24), graph_depth (1, 0-4)}.",
        "continuation_handle": "Handle from a previous answer to continue it (valid 15 minutes).",
    },
    "codecompass.architecture_overview": {
        "query": "Architecture question, e.g. 'Meet companion pipeline'.",
        "profile": "'overview' (default, about 20 nodes), 'subsystem', 'component' or 'evidence'.",
        "revision": "Revision the returned handles are bound to, default 'local'.",
    },
    "codecompass.architecture_expand": {
        "handle": "Handle 'hac:...' from architecture_overview; give handle or node.",
        "node": "Raw architecture node id; give handle or node.",
        "query": "Optional focus question.",
        "revision": "Must match the revision the handle was created with.",
    },
    "codecompass.architecture_diagram": {
        "query": "What the diagram is about, default 'architecture'.",
        "diagram_kind": "'component' (default, flowchart) or 'sequence' (needs call edges).",
        "profile": "Size of the slice, as in architecture_overview.",
    },
    "codecompass.search_symbols": {
        "query": "Symbol, file or topic to find.",
        "record_kinds": "Record kinds to keep, e.g. ['retrieval_chunk'].",
        "path_globs": "Path fragments to keep ('*' is ignored, the rest is a substring match).",
        "domain_hint": "Label copied into the records; does not filter.",
        "limit": "Maximum records, default 20, at most 100.",
    },
    "codecompass.expand_graph": {
        "node": "Graph node id (file path or symbol) to expand from.",
        "seeds": "Alternative start nodes; only the first is used.",
        "profile": "Expansion size: 'bugfix_local' (default, depth 2, 20 nodes), 'refactor_navigation' (3, 30), "
                   "'architecture_review' (3, 40) or 'config_integration' (2, 24).",
        "depth": "Ignored; use profile.",
        "max_depth": "Ignored; use profile.",
        "limit": "Ignored; use profile.",
        "max_nodes": "Ignored; use profile.",
    },
    "codecompass.get_file_context": {
        "paths": "Workspace-relative file paths, at most 8.",
        "line_ranges": "[{path, line_start, line_end}], one range per path; path must match an entry of paths.",
        "max_bytes_per_file": "Default 32768.",
        "max_total_bytes": "Default 262144 (maximum).",
        "redaction_mode": "'auto' (default, secrets redacted) or 'none'.",
        "reason": "Why the files are needed; required by policy.",
    },
    "codecompass.get_domain_map": {
        "domain_hint": "Topic of the map, default 'architecture entry points'.",
        "include_files": "Include key, test and config files, default true.",
        "include_edges": "Include graph edges between the top files, default false.",
        "max_entries": "Maximum entries, default 20, at most 100.",
    },
    "codecompass.architecture_query": {
        "question": "Query type (not free text).",
        "seed": "Start symbol or path of the query.",
        "field": "Field name for 'field-policy-impact' and 'dto-impact'.",
        "depth": "Traversal depth 1-4, default 3.",
        "direction": "'outgoing', 'incoming' or 'both'.",
    },
    "git.diff_readonly": {
        "path": "Optional workspace path to limit the diff to; unstaged changes only.",
    },
    "test.discover": {
        "limit": "Maximum test files, default 100, at most 200.",
    },
    "test.run": {
        "command": "Test command; must match an allowlisted command exactly.",
    },
    "repo.write_file": {
        "path": "Workspace-relative file path.",
        "content": "Full file text (at most 256 KiB by default).",
        "mode": "'create_only' (default, fails if the file exists) or 'replace_existing'.",
        "expected_old_hash": "sha256 of the current bytes; required for replace_existing unless the Hub approved.",
    },
    "repo.apply_patch": {
        "target_path": "Workspace-relative path of an existing file.",
        "unified_diff": "Single-file unified diff hunks (variant 'unified_diff').",
        "expected_old_hash": "Optional sha256 of the current file text.",
        "variant": "'unified_diff' or 'replace_range' (default when line_start is given).",
        "line_start": "replace_range: first line, 1-based.",
        "line_end": "replace_range: last line, inclusive; at most 120 lines.",
        "replacement": "replace_range: new text for the lines.",
        "reason": "Why the change is made; echoed back.",
    },
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "repo.read_file_range": ("path",),
    "repo.grep": ("pattern",),
    "codecompass.plan_context": ("query",),
    "codecompass.resolve_context": ("query",),
    "codecompass.search": ("query",),
    "codecompass.retrieve": ("query",),
    "codecompass.architecture_overview": ("query",),
    "codecompass.search_symbols": ("query",),
    "codecompass.get_file_context": ("paths", "reason"),
    "codecompass.architecture_query": ("question", "seed"),
    "test.run": ("command",),
    "repo.write_file": ("path", "content"),
    "repo.apply_patch": ("target_path",),
}
