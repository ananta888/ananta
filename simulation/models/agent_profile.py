"""AgentProfile schema and loader (SIM-004)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field


class AgentProfile(BaseModel):
    id: str
    name: str
    role: str = "citizen"
    personality: str = ""                    # free-text character description
    goals: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    survival_priority: float = 0.5          # 0=altruist, 1=pure survivalist
    cooperation_tendency: float = 0.5       # 0=lone wolf, 1=highly cooperative
    model_hints: dict[str, Any] = Field(default_factory=dict)
    # Private section — not included in LLM prompts
    _private: dict[str, Any] = {}

    starting_location: Optional[str] = None
    starting_inventory: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        """Fields safe to include in LLM prompts (no private data)."""
        return {
            "id": self.id, "name": self.name, "role": self.role,
            "personality": self.personality, "goals": self.goals,
            "values": self.values, "fears": self.fears,
            "survival_priority": self.survival_priority,
            "cooperation_tendency": self.cooperation_tendency,
        }


class AgentProfileLoader:
    """Load AgentProfiles from JSON/YAML/Markdown-frontmatter files."""

    def load_json(self, path: str | Path) -> AgentProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return AgentProfile.model_validate(data)

    def load_dict(self, d: dict[str, Any]) -> AgentProfile:
        return AgentProfile.model_validate(d)

    def load_directory(self, path: str | Path) -> list[AgentProfile]:
        profiles: list[AgentProfile] = []
        p = Path(path)
        for f in sorted(p.glob("*.json")):
            try:
                profiles.append(self.load_json(f))
            except Exception:
                pass
        return profiles

    def load_markdown_frontmatter(self, text: str) -> AgentProfile:
        """Parse YAML-style frontmatter between --- markers."""
        import re
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not m:
            raise ValueError("no frontmatter found")
        try:
            import yaml
            data = yaml.safe_load(m.group(1))
        except ImportError:
            # Fallback: minimal key:value parser
            data = {}
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    data[k.strip()] = v.strip()
        return AgentProfile.model_validate(data)
