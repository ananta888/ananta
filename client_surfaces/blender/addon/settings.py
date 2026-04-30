from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BlenderAddonSettings:
    endpoint: str = ''
    token_ref: str = ''
    profile: str = 'default'
