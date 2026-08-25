from agent.llm_strategies import OpenAIStrategy, get_strategy


def test_local_openai_compatible_provider_ids_use_standard_transport():
    assert isinstance(get_strategy("llamacpp"), OpenAIStrategy)
    assert isinstance(get_strategy("openai_compatible"), OpenAIStrategy)
