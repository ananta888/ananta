"""Hub-owned persistence models for visual process definitions.

``version`` remains part of the serialized domain definition.  The columns
below deliberately keep storage/schema revisions separate from that public
content version so optimistic locking can be enforced atomically by the
database.
"""
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
    definition_revision: int = Field(default=1, nullable=False, index=True)
    base_graph_hash: str = Field(default="", nullable=False, index=True)
    graph_schema_version: str = Field(default="1", nullable=False)
    node_registry_version: str = Field(default="1", nullable=False)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
