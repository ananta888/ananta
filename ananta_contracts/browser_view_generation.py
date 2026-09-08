"""Closed view-generation metadata, issued upstream; not execution authority."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserViewGeneration:
    workspace_id: str
    page_id: str
    navigation_revision: int

    def __post_init__(self):
        if any(
            type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)
            for value in (self.workspace_id, self.page_id)
        ):
            raise ValueError("browser_view_generation_invalid")
        if type(self.navigation_revision) is not int or not 1 <= self.navigation_revision <= 1023:
            raise ValueError("browser_view_generation_invalid")
