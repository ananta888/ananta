from typing import Dict, List


class PromptTemplates:
    """Registry for small string based prompt templates."""

    def __init__(self) -> None:
        self._templates: Dict[str, str] = {}

    def register(self, name: str, template: str) -> None:
        self._templates[name] = template

    def render(self, name: str, **data: str) -> str:
        template = self._templates.get(name, "")
        return template.format(**data)

    def available(self) -> List[str]:
        return list(self._templates.keys())
