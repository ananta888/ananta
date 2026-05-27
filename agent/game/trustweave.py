from __future__ import annotations

from dataclasses import asdict, dataclass


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class TrustEventResult:
    source_id: str
    target_id: str
    event_type: str
    delta: float
    trust_value: float

    def to_dict(self) -> dict[str, str | float]:
        return asdict(self)


class TrustWeaveGraph:
    _EVENT_DELTAS: dict[str, float] = {
        "verified_artifact": 0.2,
        "policy_compliant_success": 0.1,
        "verification_failed": -0.2,
        "policy_violation": -0.3,
        "neutral_observation": 0.0,
    }

    def __init__(self) -> None:
        self._edges: dict[tuple[str, str], float] = {}

    def get_trust(self, *, source_id: str, target_id: str) -> float:
        return self._edges.get((source_id, target_id), 0.0)

    def apply_event(self, *, source_id: str, target_id: str, event_type: str) -> TrustEventResult:
        delta = self._EVENT_DELTAS.get(str(event_type or "").strip().lower(), 0.0)
        edge_key = (source_id, target_id)
        updated = _clamp(self._edges.get(edge_key, 0.0) + delta)
        self._edges[edge_key] = updated
        return TrustEventResult(
            source_id=source_id,
            target_id=target_id,
            event_type=event_type,
            delta=delta,
            trust_value=updated,
        )

    def to_json_graph(self) -> dict[str, list[dict[str, str | float]]]:
        nodes = sorted({node for edge in self._edges.keys() for node in edge})
        return {
            "nodes": [{"id": node} for node in nodes],
            "edges": [
                {"source": source, "target": target, "trust_value": trust}
                for (source, target), trust in sorted(self._edges.items())
            ],
        }
