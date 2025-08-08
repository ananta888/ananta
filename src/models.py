import asyncio
import logging
from typing import Dict, Set, Tuple

logger = logging.getLogger(__name__)

class ModelPool:
    """Verwaltet die Ressourcenzuteilung für LLM-Anfragen."""

    def __init__(self):
        self.locks: Dict[str, asyncio.Lock] = {}
        self.in_use: Set[Tuple[str, str]] = set()

    async def acquire(self, provider: str, model: str) -> bool:
        """Erwirbt die Sperre für ein bestimmtes Modell.

        Args:
            provider: Der Anbieter (z.B. 'ollama', 'lmstudio')
            model: Der Modellname

        Returns:
            True bei erfolgreicher Sperrung
        """
        key = f"{provider}:{model}"
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()

        await self.locks[key].acquire()
        self.in_use.add((provider, model))
        logger.debug("Sperre erworben für %s", key)
        return True

    def release(self, provider: str, model: str) -> None:
        """Gibt die Sperre für ein Modell frei.

        Args:
            provider: Der Anbieter (z.B. 'ollama', 'lmstudio')
            model: Der Modellname
        """
        key = f"{provider}:{model}"
        if key in self.locks and self.locks[key].locked():
            self.locks[key].release()
            self.in_use.discard((provider, model))
            logger.debug("Sperre freigegeben für %s", key)
        else:
            logger.warning("Versuch, eine nicht erworbene Sperre freizugeben: %s", key)
