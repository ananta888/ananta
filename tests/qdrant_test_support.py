from __future__ import annotations

import math
from collections import defaultdict
from typing import Sequence

from worker.retrieval.qdrant_client_port import (
    ClientAvailability,
    ClientCollectionInfo,
    ClientPoint,
    ClientScoredPoint,
    QdrantClientError,
    ServerFilter,
)


class FakeQdrantClient:
    def __init__(self):
        self.collections: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.calls: dict[str, int] = defaultdict(int)
        self.failures: dict[str, list[str]] = defaultdict(list)
        self.scheduled_failures: dict[str, dict[int, str]] = defaultdict(dict)
        self.availability = ClientAvailability("ready", "ok")

    def fail_next(self, operation: str, reason: str) -> None:
        self.failures[operation].append(reason)

    def fail_on_nth_next(self, operation: str, call_number: int, reason: str) -> None:
        if int(call_number) <= 0:
            raise ValueError("call_number must be positive")
        target = self.calls[operation] + int(call_number)
        self.scheduled_failures[operation][target] = reason

    def _call(self, operation: str) -> None:
        self.calls[operation] += 1
        scheduled = self.scheduled_failures[operation].pop(
            self.calls[operation],
            None,
        )
        if scheduled is not None:
            raise QdrantClientError(scheduled, operation=operation)
        if self.failures[operation]:
            raise QdrantClientError(self.failures[operation].pop(0), operation=operation)

    def probe(self) -> ClientAvailability:
        self._call("probe")
        return self.availability

    def collection_info(self, collection_name: str) -> ClientCollectionInfo | None:
        self._call("collection_info")
        collection = self.collections.get(collection_name)
        if collection is None:
            return None
        return ClientCollectionInfo(
            collection_name,
            collection["dimensions"],
            collection["distance"],
            len(collection["points"]),
        )

    def create_collection(self, collection_name: str, *, dimensions: int, distance: str) -> None:
        self._call("create_collection")
        if collection_name in self.collections:
            raise QdrantClientError("collection_exists", operation="create_collection")
        self.collections[collection_name] = {
            "dimensions": dimensions,
            "distance": distance,
            "points": {},
        }

    def delete_collection(self, collection_name: str) -> None:
        self._call("delete_collection")
        self.collections.pop(collection_name, None)
        for alias, target in list(self.aliases.items()):
            if target == collection_name:
                del self.aliases[alias]

    def list_collections(self, *, prefix: str = "") -> tuple[str, ...]:
        self._call("list_collections")
        return tuple(sorted(name for name in self.collections if name.startswith(prefix)))

    def resolve_alias(self, alias_name: str) -> str | None:
        self._call("resolve_alias")
        return self.aliases.get(alias_name)

    def swap_alias(self, alias_name: str, collection_name: str) -> None:
        self._call("swap_alias")
        if collection_name not in self.collections:
            raise QdrantClientError("collection_missing", operation="swap_alias")
        self.aliases[alias_name] = collection_name

    def upsert(self, collection_name: str, points: Sequence[ClientPoint]) -> None:
        self._call("upsert")
        collection = self.collections[collection_name]
        for point in points:
            collection["points"][point.point_id] = point

    def retrieve(self, collection_name: str, point_ids: Sequence[str]) -> tuple[ClientPoint, ...]:
        self._call("retrieve")
        points = self.collections[collection_name]["points"]
        return tuple(points[point_id] for point_id in point_ids if point_id in points)

    @staticmethod
    def _matches(point: ClientPoint, query_filter: ServerFilter) -> bool:
        for condition in query_filter.must:
            value = point.payload.get(condition.key)
            if condition.match == "any":
                values = value if isinstance(value, list) else [value]
                if not any(item in condition.values for item in values):
                    return False
            elif isinstance(value, list):
                if condition.values[0] not in value:
                    return False
            elif value != condition.values[0]:
                return False
        return True

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        return numerator / denominator if denominator else 0.0

    def query_points(
        self,
        collection_name: str,
        *,
        query_vector: Sequence[float],
        query_filter: ServerFilter,
        limit: int,
    ) -> tuple[ClientScoredPoint, ...]:
        self._call("query_points")
        points = [
            point
            for point in self.collections[collection_name]["points"].values()
            if self._matches(point, query_filter)
        ]
        scored = [
            ClientScoredPoint(
                point.point_id,
                self._cosine(query_vector, point.vector),
                point.payload,
            )
            for point in points
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return tuple(scored[:limit])

    def delete_points(self, collection_name: str, point_ids: Sequence[str]) -> None:
        self._call("delete_points")
        for point_id in point_ids:
            self.collections[collection_name]["points"].pop(point_id, None)

    def delete_by_filter(self, collection_name: str, query_filter: ServerFilter) -> None:
        self._call("delete_by_filter")
        points = self.collections[collection_name]["points"]
        for point_id, point in list(points.items()):
            if self._matches(point, query_filter):
                del points[point_id]

    def close(self) -> None:
        self._call("close")
