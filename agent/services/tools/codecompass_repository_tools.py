"""Repository-intelligence tools exposed through CodeCompass."""

from __future__ import annotations

from typing import Any

from agent.services.tools._evidence import build_tool_result

_QUERY_MAX_RESULTS = 100
_QUERY_MAX_PATHS_PER_RESULT = 5


def rig_graph_store(arguments: dict[str, Any]):
    from ananta_codecompass.graph_store import CodeCompassGraphStore

    index_path = str(arguments.get("graph_index_path") or "").strip()
    if index_path:
        return CodeCompassGraphStore(index_path=index_path)
    return CodeCompassGraphStore(index_path=".codecompass/rig_index.json")


def codecompass_repository_query(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
) -> dict[str, Any]:
    """Run a whitelisted repository-intelligence query against the graph store."""

    from ananta_codecompass.repository_intelligence_query import (
        ALLOWED_QUERY_TYPES,
        run_query,
    )

    args = arguments or {}
    query_type = str(args.get("query_type") or "").strip()
    seed = str(args.get("seed") or "").strip()
    if not query_type:
        return build_tool_result(
            tool_name="codecompass.repository_query",
            tool_call_id=tool_call_id,
            status="error",
            error="query_type_required",
        )
    if not seed:
        return build_tool_result(
            tool_name="codecompass.repository_query",
            tool_call_id=tool_call_id,
            status="error",
            error="seed_required",
        )
    if query_type not in ALLOWED_QUERY_TYPES:
        return build_tool_result(
            tool_name="codecompass.repository_query",
            tool_call_id=tool_call_id,
            status="error",
            error=f"unsupported_query_type:{query_type}",
        )

    store = rig_graph_store(args)
    max_results = min(int(args.get("max_results", _QUERY_MAX_RESULTS)), _QUERY_MAX_RESULTS)
    try:
        result = run_query(
            graph_store=store,
            query_type=query_type,
            seed=seed,
            max_results=max_results,
        )
    except ValueError as exc:
        return build_tool_result(
            tool_name="codecompass.repository_query",
            tool_call_id=tool_call_id,
            status="error",
            error=str(exc),
        )
    return build_tool_result(
        tool_name="codecompass.repository_query",
        tool_call_id=tool_call_id,
        status="ok" if not result.warnings else "degraded",
        data={"query_result": result.as_dict()},
        warnings=list(result.warnings),
    )


def codecompass_build_test_map(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
) -> dict[str, Any]:
    """Build the repository component/dependency test map for a target."""

    from ananta_codecompass.repository_intelligence_query import run_query

    args = arguments or {}
    target = str(args.get("target") or "").strip()
    if not target:
        return build_tool_result(
            tool_name="codecompass.build_test_map",
            tool_call_id=tool_call_id,
            status="error",
            error="target_required",
        )
    store = rig_graph_store(args)
    components = run_query(graph_store=store, query_type="component-tests", seed=target)
    dependencies = run_query(graph_store=store, query_type="package-dependents", seed=target)
    evidence = sorted(set(components.evidence_paths) | set(dependencies.evidence_paths))
    evidence = evidence[:_QUERY_MAX_PATHS_PER_RESULT]
    warnings = components.warnings or dependencies.warnings
    return build_tool_result(
        tool_name="codecompass.build_test_map",
        tool_call_id=tool_call_id,
        status="ok" if not warnings else "degraded",
        data={
            "target": target,
            "components": list(components.results),
            "dependencies": list(dependencies.results),
            "evidence_paths": evidence,
            "warnings_components": list(components.warnings),
            "warnings_dependencies": list(dependencies.warnings),
        },
    )
