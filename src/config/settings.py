
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    PydanticBaseSettingsSource,
    JsonConfigSettingsSource,
)
from typing import Optional, Any, Tuple, Type
import os
import json
from pathlib import Path

class Settings(BaseSettings):
    # Agent basic config
    agent_name: str = Field(default="default", validation_alias="AGENT_NAME")
    agent_token: Optional[str] = Field(default=None, validation_alias="AGENT_TOKEN")
    port: int = Field(default=5000, validation_alias="PORT")
    role: str = Field(default="worker", validation_alias="ROLE")
    
    # Controller
    controller_url: str = Field(default="http://controller:8081", validation_alias="CONTROLLER_URL")
    
    # Database
    database_url: str = Field(default="postgresql://user:password@localhost:5432/ananta", validation_alias="DATABASE_URL")
    db_pool_size: int = Field(default=5, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, validation_alias="DB_MAX_OVERFLOW")
    
    # LLM Provider URLs
    ollama_url: str = Field(default="http://localhost:11434/api/generate", validation_alias="OLLAMA_URL")
    lmstudio_url: str = Field(default="http://localhost:1234/v1/completions", validation_alias="LMSTUDIO_URL")
    openai_url: str = Field(default="https://api.openai.com/v1/chat/completions", validation_alias="OPENAI_URL")
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    
    # LLM Defaults
    default_provider: str = Field(default="ollama", validation_alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="", validation_alias="DEFAULT_MODEL")
    
    # Logging
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool = Field(default=False, validation_alias="AI_AGENT_LOG_JSON")
    
    # Timeouts
    http_timeout: int = Field(default=30, validation_alias="HTTP_TIMEOUT")
    command_timeout: int = Field(default=60, validation_alias="COMMAND_TIMEOUT")
    
    # Retry Config
    retry_count: int = Field(default=3, validation_alias="RETRY_COUNT")
    retry_backoff: float = Field(default=1.5, validation_alias="RETRY_BACKOFF")

    # Feature Flags
    feature_history_enabled: bool = Field(default=True, validation_alias="FEATURE_HISTORY_ENABLED")
    feature_load_balancing_enabled: bool = Field(default=True, validation_alias="FEATURE_LOAD_BALANCING_ENABLED")
    feature_robust_http_enabled: bool = Field(default=True, validation_alias="FEATURE_ROBUST_HTTP_ENABLED")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            JsonConfigSettingsSource(settings_cls, json_file="env.json"),
            JsonConfigSettingsSource(settings_cls, json_file="defaults.json"),
        )

# Instanz erstellen
try:
    settings = Settings()
except Exception as e:
    print(f"Error loading settings: {e}")
    # Minimaler Fallback falls Pydantic wegen Validierung fehlschlägt
    # (z.B. wenn Defaults nicht zum Typ passen, was hier nicht passieren sollte)
    settings = Settings.model_construct()
