from __future__ import annotations

"""Simple prompt template management."""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class PromptTemplates:
    """Manage named prompt templates.

    The class stores a mapping of template names to template strings and
    provides a :meth:`render` method which formats a template with provided
    keyword arguments.
    """

    templates: Dict[str, str] = field(default_factory=dict)

    def add(self, name: str, template: str) -> None:
        """Register or update a template."""
        self.templates[name] = template

    def render(self, template_name: str, **kwargs: str) -> str:
        """Render a template by ``template_name`` with ``kwargs``.

        Missing template names return an empty string.
        """
        text = self.templates.get(template_name, "")
        try:
            return text.format(**kwargs)
        except Exception:
            # In case of formatting errors, return the template verbatim
            return text
