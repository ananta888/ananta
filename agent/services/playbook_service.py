from __future__ import annotations
import time
from typing import List, Optional, Dict, Any

from agent.db_models import PlaybookDB
from agent.services.repository_registry import get_repository_registry
from agent.services.planning_utils import GOAL_TEMPLATES

class PlaybookService:
    def get_all_playbooks(self) -> List[PlaybookDB]:
        repos = get_repository_registry()
        return repos.playbook_repo.get_all()

    def get_playbook_by_id(self, playbook_id: str) -> Optional[PlaybookDB]:
        repos = get_repository_registry()
        return repos.playbook_repo.get_by_id(playbook_id)

    def get_playbook_by_name(self, name: str) -> Optional[PlaybookDB]:
        repos = get_repository_registry()
        return repos.playbook_repo.get_by_name(name)

    def create_playbook(self, name: str, description: str = None, tasks: List[dict] = None) -> PlaybookDB:
        repos = get_repository_registry()
        playbook = PlaybookDB(
            name=name,
            description=description,
            tasks=tasks or []
        )
        return repos.playbook_repo.save(playbook)

    def initialize_standard_playbooks(self):
        for name, data in GOAL_TEMPLATES.items():
            if not self.get_playbook_by_name(name):
                self.create_playbook(
                    name=name,
                    description=f"Standard-Workflow fuer {name.replace('_', ' ').title()}",
                    tasks=data["subtasks"]
                )

_playbook_service = PlaybookService()

def get_playbook_service() -> PlaybookService:
    return _playbook_service
