import os
from getpass import getpass
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from click import UsageError

try:
    from agent.config import settings
except ImportError:
    settings = None

CONFIG_FOLDER = os.path.expanduser("~/.config")
SHELL_GPT_CONFIG_FOLDER = Path(CONFIG_FOLDER) / "shell_gpt"
SHELL_GPT_CONFIG_PATH = SHELL_GPT_CONFIG_FOLDER / ".sgptrc"
ROLE_STORAGE_PATH = SHELL_GPT_CONFIG_FOLDER / "roles"
FUNCTIONS_PATH = SHELL_GPT_CONFIG_FOLDER / "functions"
CHAT_CACHE_PATH = Path(gettempdir()) / "chat_cache"
CACHE_PATH = Path(gettempdir()) / "cache"

# Default provider mapping from global settings
_default_model = "gpt-4o"
_api_base_url = "default"
_api_key = os.getenv("OPENAI_API_KEY")
_prettify_markdown = "true"
_shell_interaction = "true"
_code_theme = "dracula"
_default_color = "magenta"
_use_litellm = "false"

if settings:
    _default_model = getattr(settings, 'sgpt_default_model', _default_model)
    if not _default_model and settings.default_model:
        _default_model = settings.default_model

    _prettify_markdown = "true" if getattr(settings, 'sgpt_prettify_markdown', True) else "false"
    _shell_interaction = "true" if getattr(settings, 'sgpt_shell_interaction', True) else "false"
    _code_theme = getattr(settings, 'sgpt_code_theme', _code_theme)
    _default_color = getattr(settings, 'sgpt_default_color', _default_color)
    _use_litellm = "true" if getattr(settings, 'sgpt_use_litellm', False) else "false"

    # Map global provider URLs to SGPT API_BASE_URL
    if settings.default_provider == "openai":
        _api_base_url = settings.openai_url
        _api_key = settings.openai_api_key or _api_key
    elif settings.default_provider == "ollama":
        _api_base_url = settings.ollama_url
    elif settings.default_provider == "lmstudio":
        _api_base_url = settings.lmstudio_url
    elif settings.default_provider == "anthropic":
        _api_base_url = settings.anthropic_url
        _api_key = settings.anthropic_api_key or _api_key

DEFAULT_CONFIG = {
    "CHAT_CACHE_PATH": os.getenv("CHAT_CACHE_PATH", str(CHAT_CACHE_PATH)),
    "CACHE_PATH": os.getenv("CACHE_PATH", str(CACHE_PATH)),
    "CHAT_CACHE_LENGTH": int(os.getenv("CHAT_CACHE_LENGTH", "100")),
    "CACHE_LENGTH": int(os.getenv("CHAT_CACHE_LENGTH", "100")),
    "REQUEST_TIMEOUT": int(os.getenv("REQUEST_TIMEOUT", str(getattr(settings, 'http_timeout', 60)))),
    "DEFAULT_MODEL": os.getenv("DEFAULT_MODEL", _default_model),
    "DEFAULT_COLOR": os.getenv("DEFAULT_COLOR", _default_color),
    "ROLE_STORAGE_PATH": os.getenv("ROLE_STORAGE_PATH", str(ROLE_STORAGE_PATH)),
    "DEFAULT_EXECUTE_SHELL_CMD": os.getenv("DEFAULT_EXECUTE_SHELL_CMD", "false"),
    "DISABLE_STREAMING": os.getenv("DISABLE_STREAMING", "false"),
    "CODE_THEME": os.getenv("CODE_THEME", _code_theme),
    "OPENAI_FUNCTIONS_PATH": os.getenv("OPENAI_FUNCTIONS_PATH", str(FUNCTIONS_PATH)),
    "OPENAI_USE_FUNCTIONS": os.getenv("OPENAI_USE_FUNCTIONS", "true"),
    "SHOW_FUNCTIONS_OUTPUT": os.getenv("SHOW_FUNCTIONS_OUTPUT", "false"),
    "API_BASE_URL": os.getenv("API_BASE_URL", _api_base_url),
    "OPENAI_API_KEY": _api_key,
    "PRETTIFY_MARKDOWN": os.getenv("PRETTIFY_MARKDOWN", _prettify_markdown),
    "USE_LITELLM": os.getenv("USE_LITELLM", _use_litellm),
    "SHELL_INTERACTION": os.getenv("SHELL_INTERACTION", _shell_interaction),
    "OS_NAME": os.getenv("OS_NAME", "auto"),
    "SHELL_NAME": os.getenv("SHELL_NAME", "auto"),
}


class Config(dict):  # type: ignore
    def __init__(self, config_path: Path, **defaults: Any):
        self.config_path = config_path

        if self._exists:
            self._read()
            has_new_config = False
            for key, value in defaults.items():
                if key not in self:
                    has_new_config = True
                    self[key] = value
            if has_new_config:
                self._write()
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            # SGPT-4: Skip interactive getpass in service mode or non-TTY.
            # We assume it is service mode if `settings` is present or it is not a TTY.
            import sys
            is_tty = sys.stdin.isatty()
            if not defaults.get("OPENAI_API_KEY") and not os.getenv("OPENAI_API_KEY"):
                if settings or not is_tty:
                    # Allow empty API key when using local providers like Ollama.
                    # Or when running as a service where we can't prompt.
                    defaults["OPENAI_API_KEY"] = ""
                else:
                    __api_key = getpass(prompt="Please enter your OpenAI API key: ")
                    defaults["OPENAI_API_KEY"] = __api_key
            super().__init__(**defaults)
            self._write()

    @property
    def _exists(self) -> bool:
        return self.config_path.exists()

    def _write(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as file:
            string_config = ""
            for key, value in self.items():
                string_config += f"{key}={value}\n"
            file.write(string_config)

    def _read(self) -> None:
        with open(self.config_path, "r", encoding="utf-8") as file:
            for line in file:
                if line.strip() and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    self[key] = value

    def get(self, key: str) -> str:  # type: ignore
        # Prioritize environment variables over config file.
        value = os.getenv(key) or super().get(key)
        if not value:
            raise UsageError(f"Missing config key: {key}")
        return value


cfg = Config(SHELL_GPT_CONFIG_PATH, **DEFAULT_CONFIG)
