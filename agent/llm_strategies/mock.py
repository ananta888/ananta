from typing import Optional, Any
from agent.llm_strategies.base import LLMStrategy
import logging

class MockStrategy(LLMStrategy):
    """
    Mock-LLM-Strategie für Tests.
    Gibt vordefinierte Antworten zurück, um Abhängigkeiten von echten APIs zu vermeiden.
    """
    def execute(
        self,
        model: str,
        prompt: str,
        url: str,
        api_key: Optional[str],
        history: Optional[list],
        timeout: int,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None
    ) -> Any:
        logging.info(f"Mock-LLM aufgerufen mit Prompt: {prompt[:50]}...")
        
        # Einfache Logik für Mock-Antworten
        prompt_lower = prompt.lower()
        if "hallo" in prompt_lower or "hello" in prompt_lower:
            return "Hallo! Ich bin ein Mock-LLM. Wie kann ich dir helfen?"
        if "list files" in prompt_lower:
            return "Hier ist eine Liste der Dateien: README.md, todo.json, agent/"
        if "error" in prompt_lower:
            return "" # Simuliere leere Antwort/Fehler
            
        return f"MOCK_RESPONSE: Ich habe deinen Prompt erhalten: '{prompt[:30]}...'"
