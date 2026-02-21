from agent.llm_strategies.base import LLMStrategy
from agent.llm_strategies.lmstudio import LMStudioStrategy
from agent.llm_strategies.mock import MockStrategy
from agent.llm_strategies.standard import AnthropicStrategy, OllamaStrategy, OpenAIStrategy

STRATEGIES = {
    "openai": OpenAIStrategy(),
    "anthropic": AnthropicStrategy(),
    "ollama": OllamaStrategy(),
    "lmstudio": LMStudioStrategy(),
    "mock": MockStrategy(),
}


def get_strategy(provider: str) -> LLMStrategy:
    return STRATEGIES.get(provider.lower())
