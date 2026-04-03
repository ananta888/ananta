from __future__ import annotations

try:
    from agent.config import settings
    from agent.metrics import SHELL_POOL_BUSY, SHELL_POOL_FREE, SHELL_POOL_SIZE
except (ImportError, ModuleNotFoundError):
    try:
        from config import settings
        from metrics import SHELL_POOL_BUSY, SHELL_POOL_FREE, SHELL_POOL_SIZE
    except (ImportError, ModuleNotFoundError):
        class MockMetric:
            def set(self, val):
                pass

        SHELL_POOL_SIZE = SHELL_POOL_BUSY = SHELL_POOL_FREE = MockMetric()
        if "settings" not in locals():
            class MockSettings:
                shell_path = None
                shell_pool_size = 5
                enable_advanced_command_analysis = False
                fail_secure_llm_analysis = False
                default_provider = "ollama"
                default_model = "llama3"
                ollama_url = "http://localhost:11434/api/generate"
                lmstudio_url = "http://192.168.56.1:1234/v1/completions"
                openai_url = "https://api.openai.com/v1/chat/completions"
                anthropic_url = "https://api.anthropic.com/v1/messages"
                openai_api_key = None

            settings = MockSettings()
