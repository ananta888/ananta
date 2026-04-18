from __future__ import annotations
import time
from typing import List, Optional

from agent.db_models import ActionPackDB
from agent.services.repository_registry import get_repository_registry

class ActionPackService:
    def get_all_action_packs(self, enabled_only: bool = False) -> List[ActionPackDB]:
        repos = get_repository_registry()
        return repos.action_pack_repo.get_all(enabled_only=enabled_only)

    def get_action_pack_by_id(self, action_pack_id: str) -> Optional[ActionPackDB]:
        repos = get_repository_registry()
        return repos.action_pack_repo.get_by_id(action_pack_id)

    def get_action_pack_by_name(self, name: str) -> Optional[ActionPackDB]:
        repos = get_repository_registry()
        return repos.action_pack_repo.get_by_name(name)

    def create_action_pack(self, name: str, description: str = None, capabilities: List[str] = None, policy_config: dict = None) -> ActionPackDB:
        repos = get_repository_registry()
        pack = ActionPackDB(
            name=name,
            description=description,
            capabilities=capabilities or [],
            policy_config=policy_config or {}
        )
        return repos.action_pack_repo.save(pack)

    def update_action_pack(self, action_pack_id: str, **kwargs) -> Optional[ActionPackDB]:
        repos = get_repository_registry()
        pack = repos.action_pack_repo.get_by_id(action_pack_id)
        if not pack:
            return None

        for key, value in kwargs.items():
            if hasattr(pack, key):
                setattr(pack, key, value)

        pack.updated_at = time.time()
        return repos.action_pack_repo.save(pack)

    def toggle_action_pack(self, action_pack_id: str, enabled: bool) -> Optional[ActionPackDB]:
        return self.update_action_pack(action_pack_id, enabled=enabled)

    def initialize_action_packs(self):
        from agent.services.platform_governance_service import _DEFAULT_ACTION_PACKS

        for name, defaults in _DEFAULT_ACTION_PACKS.items():
            if not self.get_action_pack_by_name(name):
                self.create_action_pack(
                    name=name,
                    description=defaults["description"],
                    capabilities=defaults["capabilities"],
                    policy_config={"enabled_by_default": defaults["enabled_by_default"]}
                )

_action_pack_service = ActionPackService()

def get_action_pack_service() -> ActionPackService:
    return _action_pack_service
