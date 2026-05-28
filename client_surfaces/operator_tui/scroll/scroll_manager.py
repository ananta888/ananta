from __future__ import annotations

from typing import Any

from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext


class ScrollManager:
    def __init__(self) -> None:
        self._contexts: dict[str, ScrollContext] = {}
        self._order: list[str] = []

    def register(self, ctx: ScrollContext) -> None:
        if ctx.id not in self._contexts:
            self._order.append(ctx.id)
        self._contexts[ctx.id] = ctx

    def update(self, ctx_id: str, **kwargs: Any) -> bool:
        ctx = self._contexts.get(ctx_id)
        if ctx is None:
            return False
        for k, v in kwargs.items():
            if hasattr(ctx, k):
                setattr(ctx, k, v)
        ctx.clamp()
        return True

    def remove(self, ctx_id: str) -> None:
        self._contexts.pop(ctx_id, None)
        try:
            self._order.remove(ctx_id)
        except ValueError:
            pass

    def get(self, ctx_id: str) -> ScrollContext | None:
        return self._contexts.get(ctx_id)

    def focusable_contexts(self) -> list[ScrollContext]:
        return [
            self._contexts[cid]
            for cid in self._order
            if cid in self._contexts and self._contexts[cid].focusable and self._contexts[cid].visible
        ]

    def visible_contexts(self) -> list[ScrollContext]:
        return [
            self._contexts[cid]
            for cid in self._order
            if cid in self._contexts and self._contexts[cid].visible
        ]

    def remove_stale(self, active_ids: set[str]) -> list[str]:
        removed = [cid for cid in list(self._order) if cid not in active_ids]
        for cid in removed:
            self.remove(cid)
        return removed

    def all_ids(self) -> list[str]:
        return list(self._order)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "total": len(self._contexts),
            "focusable": len(self.focusable_contexts()),
            "contexts": {cid: ctx.diagnostics() for cid, ctx in self._contexts.items()},
        }
