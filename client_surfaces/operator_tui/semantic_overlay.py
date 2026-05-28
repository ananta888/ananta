"""Semantic Overlay — extrahiert logische Panel-Bounding-Boxes und Entities aus OperatorState."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PanelBBox:
    section_id: str
    semantic_id: str
    x: int; y: int; w: int; h: int
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"section_id": self.section_id, "semantic_id": self.semantic_id,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h, "is_active": self.is_active}

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass
class SemanticEntity:
    kind: str  # snake_head, snake_body, mouse, artifact
    semantic_id: str
    x: int; y: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "semantic_id": self.semantic_id,
                "x": self.x, "y": self.y, "metadata": self.metadata}


@dataclass
class SemanticOverlay:
    panels: list[PanelBBox] = field(default_factory=list)
    active_panel: str | None = None
    entities: list[SemanticEntity] = field(default_factory=list)
    screen_hash: str = ""
    semantic_hash: str = ""

    def __post_init__(self) -> None:
        if not self.semantic_hash:
            self.semantic_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        import hashlib, json
        canonical = json.dumps({
            "panels": [p.to_dict() for p in self.panels],
            "active_panel": self.active_panel,
            "entities": [e.to_dict() for e in self.entities],
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def panel_at(self, x: int, y: int) -> PanelBBox | None:
        for p in self.panels:
            if p.contains(x, y):
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "panels": [p.to_dict() for p in self.panels],
            "active_panel": self.active_panel,
            "entities": [e.to_dict() for e in self.entities],
            "screen_hash": self.screen_hash,
            "semantic_hash": self.semantic_hash,
        }


def build_from_operator_state(
    state: dict[str, Any],
    *,
    width: int = 120,
    body_start: int = 8,
    body_end: int = 30,
    screen_hash: str = "",
) -> SemanticOverlay:
    """Erstellt SemanticOverlay aus OperatorState-Daten (kein Renderer-Hack)."""
    panels: list[PanelBBox] = []
    entities: list[SemanticEntity] = []

    active_panel = str(state.get("active_panel") or "").strip() or None
    header_h = body_start
    body_h = max(0, body_end - body_start)

    # Standard-Panels nach TUI-Layout
    panels.append(PanelBBox("HEADER", "header", 0, 0, width, header_h, is_active=(active_panel == "HEADER")))
    panels.append(PanelBBox("BODY", "body", 0, body_start, width, body_h, is_active=(active_panel == "BODY")))

    # Snake-Positionen aus game-state
    game = dict(state.get("header_logo_game") or {})
    snakes = game.get("snakes") or []
    for snake_idx, snake in enumerate(snakes):
        body = snake.get("body") or []
        for seg_idx, seg in enumerate(body):
            if not isinstance(seg, (list, tuple)) or len(seg) < 2:
                continue
            kind = "snake_head" if seg_idx == 0 else "snake_body"
            entities.append(SemanticEntity(
                kind=kind,
                semantic_id=f"snake_{snake_idx}_seg_{seg_idx}",
                x=int(seg[0]), y=int(seg[1]),
                metadata={"snake_idx": snake_idx, "seg_idx": seg_idx},
            ))

    # Mouse-State
    mx = state.get("mouse_x")
    my = state.get("mouse_y")
    if mx is not None and my is not None:
        entities.append(SemanticEntity("mouse", "mouse_cursor", int(mx), int(my)))

    overlay = SemanticOverlay(
        panels=panels,
        active_panel=active_panel,
        entities=entities,
        screen_hash=screen_hash,
    )
    return overlay
