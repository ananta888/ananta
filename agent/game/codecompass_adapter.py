from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import CodeTerritory, ContextGate, GameMap, TrustEdge


def _territory_id(path: str) -> str:
    normalized = str(path or "").strip().strip("/")
    return f"territory:{normalized or 'unknown'}"


def build_game_map_from_repository(
    *,
    repo_paths: Iterable[str],
    dependency_edges: Iterable[tuple[str, str]] | None = None,
    risk_overrides: Mapping[str, str] | None = None,
    context_overrides: Mapping[str, Mapping[str, object]] | None = None,
    map_id: str = "map:ananta-strategy",
    title: str = "Ananta Strategy Map",
) -> GameMap:
    risks = dict(risk_overrides or {})
    contexts = {str(key): dict(value) for key, value in (context_overrides or {}).items()}

    territories: list[CodeTerritory] = []
    context_gates: list[ContextGate] = []
    seen: set[str] = set()

    for raw_path in repo_paths:
        path = str(raw_path or "").strip().strip("/")
        if not path or path in seen:
            continue
        seen.add(path)
        territory = CodeTerritory(
            id=_territory_id(path),
            name=path,
            path=path,
            module=path.split("/", 1)[0] if "/" in path else path,
            risk_level=str(risks.get(path) or "medium"),
        )
        territories.append(territory)

        gate_payload = contexts.get(path) or {}
        context_gates.append(
            ContextGate(
                id=f"context:{territory.id}",
                territory_id=territory.id,
                visibility=str(gate_payload.get("visibility") or "hidden"),
                allowed_roles=tuple(str(item) for item in gate_payload.get("allowed_roles") or ()),
                local_only=bool(gate_payload.get("local_only", False)),
                secret=bool(gate_payload.get("secret", False)),
            )
        )

    trust_edges: list[TrustEdge] = []
    for source, target in dependency_edges or ():
        source_path = str(source or "").strip().strip("/")
        target_path = str(target or "").strip().strip("/")
        if not source_path or not target_path:
            continue
        trust_edges.append(
            TrustEdge(
                id=f"dep:{source_path}->{target_path}",
                source_id=_territory_id(source_path),
                target_id=_territory_id(target_path),
                relationship="dependency",
                direction="directed",
                trust_value=0.5,
            )
        )

    if not territories:
        return GameMap(
            id=map_id,
            title=title,
            territories=(),
            context_gates=(),
            trust_edges=tuple(trust_edges),
            degraded=True,
            metadata={"reason": "missing_repo_paths"},
        )

    return GameMap(
        id=map_id,
        title=title,
        territories=tuple(territories),
        context_gates=tuple(context_gates),
        trust_edges=tuple(trust_edges),
        degraded=False,
        metadata={"source": "repository_metadata"},
    )
