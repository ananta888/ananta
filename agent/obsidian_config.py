"""Obsidian Vault Profile configuration for ANANTA (OBS-001)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator


class ObsidianVaultProfile(BaseModel):
    path: str
    name: str = ""  # wird aus dict-key gesetzt
    enabled: bool = True
    read_only: bool = True
    description: Optional[str] = None
    exclude_dirs: list[str] = [".obsidian", ".git", ".trash", "assets"]
    exclude_glob_patterns: list[str] = []
    private_path_prefixes: list[str] = ["private/", "personal/", "_private"]
    private_frontmatter_field: str = "private"
    private_frontmatter_truthy_values: list = [True, "true", "yes", "1"]
    private_tags: list[str] = ["no-index", "private", "personal"]
    privacy_filter_mode: str = "or"  # or/and/off
    index_canvas_files: bool = True
    index_dataview_as_metadata: bool = True
    resolve_aliases: bool = True
    heading_chunk_level: int = 2
    max_block_size_chars: int = 2000
    min_block_size_chars: int = 50
    tag_namespace_separator: str = "/"
    tag_max_depth: Optional[int] = None
    write_back_enabled: bool = False
    write_back_folder: str = "_ananta"
    write_back_overwrite: bool = False

    @field_validator("privacy_filter_mode")
    @classmethod
    def validate_privacy_mode(cls, v: str) -> str:
        if v not in ("or", "and", "off"):
            raise ValueError(f"privacy_filter_mode must be 'or', 'and', or 'off', got {v!r}")
        return v

    @field_validator("heading_chunk_level")
    @classmethod
    def validate_heading_level(cls, v: int) -> int:
        if not 1 <= v <= 6:
            raise ValueError(f"heading_chunk_level must be 1-6, got {v}")
        return v


def load_vault_profiles(raw: dict) -> dict[str, ObsidianVaultProfile]:
    """Parse a dict of {name: config_dict} into {name: ObsidianVaultProfile}."""
    profiles: dict[str, ObsidianVaultProfile] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        cfg = dict(cfg)
        cfg.setdefault("name", name)
        profiles[name] = ObsidianVaultProfile(**cfg)
    return profiles
