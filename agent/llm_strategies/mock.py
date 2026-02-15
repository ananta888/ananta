import json
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

        prompt_lower = prompt.lower()

        # JSON-Antworten für strukturierte Requests
        if "bearbeite task" in prompt_lower or "auftrag" in prompt_lower or "ls -la" in prompt_lower:
            return json.dumps({
                "reason": "Ich werde die Dateien im aktuellen Verzeichnis auflisten, um einen Überblick zu erhalten.",
                "command": "ls -la"
            })

        if "hallo" in prompt_lower or "hello" in prompt_lower:
            return json.dumps({
                "reason": "Begrüßung des Users.",
                "command": "echo 'Hallo! Ich bin der Ananta Mock-LLM-Provider.'"
            })

        if "list files" in prompt_lower:
            return json.dumps({
                "reason": "Dateien auflisten angefordert.",
                "command": "ls"
            })

        if "error" in prompt_lower:
            return "" # Simuliere leere Antwort/Fehler

        # Fallback für unerkannte Prompts
        return json.dumps({
            "reason": f"Mock-Antwort auf: {prompt[:30]}...",
            "command": "echo 'MOCK_OK'"
        })
