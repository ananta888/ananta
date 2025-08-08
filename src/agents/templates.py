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
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class PromptTemplates:
    """Verwaltet und rendert Prompt-Templates für verschiedene Agenten."""

    def __init__(self, templates: Dict[str, str]):
        self.templates = templates

    def render(self, template_name: str, **kwargs: Any) -> Optional[str]:
        """Rendert ein Prompt-Template mit den gegebenen Variablen.

        Args:
            template_name: Name des Templates
            **kwargs: Variablen zum Ersetzen im Template

        Returns:
            Der gerenderte Prompt oder None, wenn das Template nicht existiert
        """
        template = self.templates.get(template_name)
        if not template:
            logger.warning("Template '%s' nicht gefunden", template_name)
            return None

        # Einfache String-Ersetzung für Variablen
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error("Fehlende Variable %s im Template '%s'", e, template_name)
            return None
        except Exception as e:
            logger.error("Fehler beim Rendern des Templates '%s': %s", template_name, e)
            return None
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
