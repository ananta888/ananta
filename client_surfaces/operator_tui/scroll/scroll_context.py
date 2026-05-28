from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScrollContext:
    id: str
    label: str
    content_height: int
    viewport_height: int
    offset: int = 0
    focusable: bool = True
    visible: bool = True
    page_overlap: int = 1

    def __post_init__(self) -> None:
        self.clamp()

    @property
    def max_scroll(self) -> int:
        return max(0, self.content_height - self.viewport_height)

    def clamp(self) -> None:
        self.offset = max(0, min(self.offset, self.max_scroll))

    def scroll_line_up(self, n: int = 1) -> bool:
        old = self.offset
        self.offset = max(0, self.offset - max(1, n))
        return self.offset != old

    def scroll_line_down(self, n: int = 1) -> bool:
        old = self.offset
        self.offset = min(self.max_scroll, self.offset + max(1, n))
        return self.offset != old

    def scroll_page_up(self) -> bool:
        page = max(1, self.viewport_height - self.page_overlap)
        return self.scroll_line_up(page)

    def scroll_page_down(self) -> bool:
        page = max(1, self.viewport_height - self.page_overlap)
        return self.scroll_line_down(page)

    def scroll_home(self) -> bool:
        old = self.offset
        self.offset = 0
        return self.offset != old

    def scroll_end(self) -> bool:
        old = self.offset
        self.offset = self.max_scroll
        return self.offset != old

    def is_at_bottom(self, tolerance: int = 2) -> bool:
        return self.max_scroll == 0 or self.offset >= self.max_scroll - tolerance

    def is_scrollable(self) -> bool:
        return self.content_height > self.viewport_height

    def update_dimensions(self, *, content_height: int, viewport_height: int) -> None:
        self.content_height = max(0, content_height)
        self.viewport_height = max(1, viewport_height)
        self.clamp()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "offset": self.offset,
            "max_scroll": self.max_scroll,
            "content_height": self.content_height,
            "viewport_height": self.viewport_height,
            "scrollable": self.is_scrollable(),
            "at_bottom": self.is_at_bottom(),
        }
