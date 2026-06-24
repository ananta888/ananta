"""DB model for persisting VisualProcessGraph as JSON blobs (VPPERS-001)."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from sqlmodel import Field, SQLModel


class VisualProcessGraphDB(SQLModel, table=True):
    __tablename__ = "visual_process_graphs"

    id: str = Field(
        default_factory=lambda: f"vp-{uuid.uuid4().hex[:8]}",
        primary_key=True,
    )
    name: str
    description: str = ""
    tags: str = ""          # comma-separated tag list
    graph_json: str = ""    # full VisualProcessGraph.model_dump() as JSON string
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
