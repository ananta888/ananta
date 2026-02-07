
from pydantic import Field, AliasChoices, field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    PydanticBaseSettingsSource,
    JsonConfigSettingsSource,
)
from typing import Optional, Any, Tuple, Type
import os
import json
import logging
from pathlib import Path

class Settings(BaseSettings):
    # Agent basic config
    agent_name: str = Field(default="default", validation_alias="AGENT_NAME")
    agent_token: Optional[str] = Field(default=None, validation_alias="AGENT_TOKEN")
    port: int = Field(default=5000, validation_alias="PORT")
    role: str = Field(default="worker", validation_alias="ROLE")
    agent_url: Optional[str] = Field(default=None, validation_alias="AGENT_URL")
    
    # Hub
    hub_url: str = Field(default="http://localhost:5000", validation_alias=AliasChoices("HUB_URL", "CONTROLLER_URL"))
    
    # LLM Provider URLs
    ollama_url: str = Field(default="http://localhost:11434/api/generate", validation_alias="OLLAMA_URL")
    lmstudio_url: str = Field(default="http://localhost:1234/v1", validation_alias="LMSTUDIO_URL")
    lmstudio_api_mode: str = Field(default="chat", validation_alias="LMSTUDIO_API_MODE")
    openai_url: str = Field(default="https://api.openai.com/v1/chat/completions", validation_alias="OPENAI_URL")
    anthropic_url: str = Field(default="https://api.anthropic.com/v1/messages", validation_alias="ANTHROPIC_URL")
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    
    # LLM Defaults
    default_provider: str = Field(default="ollama", validation_alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="", validation_alias="DEFAULT_MODEL")
    lmstudio_max_context_tokens: int = Field(default=4096, validation_alias="LMSTUDIO_MAX_CONTEXT_TOKENS")
    
    # Logging
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool = Field(default=False, validation_alias="AI_AGENT_LOG_JSON")
    
    # Shell
    shell_path: Optional[str] = Field(default=None, validation_alias="SHELL_PATH")
    shell_pool_size: int = Field(default=5, validation_alias="SHELL_POOL_SIZE")
    
    # Timeouts
    http_timeout: int = Field(default=30, validation_alias="HTTP_TIMEOUT")
    command_timeout: int = Field(default=60, validation_alias="COMMAND_TIMEOUT")
    agent_offline_timeout: int = Field(default=300, validation_alias="AGENT_OFFLINE_TIMEOUT")
    
    # Retry Config
    retry_count: int = Field(default=3, validation_alias="RETRY_COUNT")
    retry_backoff: float = Field(default=1.5, validation_alias="RETRY_BACKOFF")
    
    # Feature Flags
    feature_history_enabled: bool = Field(default=True, validation_alias="FEATURE_HISTORY_ENABLED")
    feature_load_balancing_enabled: bool = Field(default=True, validation_alias="FEATURE_LOAD_BALANCING_ENABLED")
    feature_robust_http_enabled: bool = Field(default=True, validation_alias="FEATURE_ROBUST_HTTP_ENABLED")

    # Extensions
    extensions: str = Field(default="", validation_alias="AGENT_EXTENSIONS")
    
    # Security
    secret_key: str = Field(default="", validation_alias="SECRET_KEY")
    mfa_encryption_key: Optional[str] = Field(default=None, validation_alias="MFA_ENCRYPTION_KEY")
    cors_origins: str = Field(default="*", validation_alias="CORS_ORIGINS")
    registration_token: Optional[str] = Field(default=None, validation_alias="REGISTRATION_TOKEN")
    token_rotation_days: int = Field(default=7, validation_alias="TOKEN_ROTATION_DAYS")
    enable_advanced_command_analysis: bool = Field(default=False, validation_alias="ENABLE_ADVANCED_COMMAND_ANALYSIS")
    fail_secure_llm_analysis: bool = Field(default=False, validation_alias="FAIL_SECURE_LLM_ANALYSIS")
    disable_llm_check: bool = Field(default=False, validation_alias="DISABLE_LLM_CHECK")
    
    # Initial User
    initial_admin_user: str = Field(default="admin", validation_alias="INITIAL_ADMIN_USER")
    initial_admin_password: Optional[str] = Field(default="admin", validation_alias="INITIAL_ADMIN_PASSWORD")
    disable_initial_admin: bool = Field(default=False, validation_alias="DISABLE_INITIAL_ADMIN")
    
    # SGPT Config
    sgpt_default_model: str = Field(default="gpt-4o", validation_alias="SGPT_DEFAULT_MODEL")
    sgpt_default_color: str = Field(default="magenta", validation_alias="SGPT_DEFAULT_COLOR")
    sgpt_code_theme: str = Field(default="dracula", validation_alias="SGPT_CODE_THEME")
    sgpt_prettify_markdown: bool = Field(default=True, validation_alias="SGPT_PRETTIFY_MARKDOWN")
    sgpt_use_litellm: bool = Field(default=False, validation_alias="SGPT_USE_LITELLM")
    sgpt_shell_interaction: bool = Field(default=True, validation_alias="SGPT_SHELL_INTERACTION")
    
    # Database
    database_url: Optional[str] = Field(default=None, validation_alias="DATABASE_URL")

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = os.path.join(self.data_dir, "ananta.db")
        return f"sqlite:///{db_path}"
    
    # Redis
    redis_url: Optional[str] = Field(default=None, validation_alias="REDIS_URL")
    
    # Task Archiving
    tasks_retention_days: int = Field(default=30, validation_alias="TASKS_RETENTION_DAYS")
    stats_history_size: int = Field(default=60, validation_alias="STATS_HISTORY_SIZE")

    # Paths
    data_dir: str = Field(default="data", validation_alias="DATA_DIR")
    secrets_dir: str = Field(default="secrets", validation_alias="SECRETS_DIR")
    
    @field_validator("lmstudio_api_mode")
    @classmethod
    def validate_lmstudio_api_mode(cls, v: str) -> str:
        allowed = ["chat", "completions"]
        if v.lower() not in allowed:
            raise ValueError(f"LMSTUDIO_API_MODE muss einer der folgenden Werte sein: {allowed}")
        return v.lower()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        secrets_dir="secrets", # Standardmäßig im Unterordner secrets/
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
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            file_secret_settings,
            dotenv_settings,
        ]
        # Optionale JSON-Quellen nur hinzufügen, wenn vorhanden
        if Path("config.json").exists():
            sources.append(JsonConfigSettingsSource(settings_cls, json_file="config.json"))
        if Path("env.json").exists():
            sources.append(JsonConfigSettingsSource(settings_cls, json_file="env.json"))
        if Path("defaults.json").exists():
            sources.append(JsonConfigSettingsSource(settings_cls, json_file="defaults.json"))
        return tuple(sources)

# Instanz erstellen
try:
    settings = Settings()
    
    # Post-init validation and security checks
    logger = logging.getLogger("agent.config")
    import secrets
    
    # 1. SECRET_KEY Handling
    if not settings.secret_key:
        # Versuche aus secrets_dir zu laden, falls Pydantic es nicht automatisch getan hat
        secret_key_path = Path(settings.secrets_dir) / "secret_key"
        
        if secret_key_path.exists():
            try:
                settings.secret_key = secret_key_path.read_text().strip()
                logger.info(f"SECRET_KEY loaded from {secret_key_path}")
            except Exception as e:
                logger.error(f"Could not read SECRET_KEY from {secret_key_path}: {e}")
        
        if not settings.secret_key:
            # Generiere einen zufälligen Key, falls keiner angegeben wurde oder geladen werden konnte
            settings.secret_key = secrets.token_urlsafe(32)
            logger.warning("SECRET_KEY was not set. A random key has been generated.")
            
            # Versuche den generierten Key zu persistieren
            try:
                os.makedirs(settings.secrets_dir, exist_ok=True)
                secret_key_path.write_text(settings.secret_key)
                logger.info(f"Generated SECRET_KEY persisted to {secret_key_path}")
            except Exception as e:
                logger.error(f"Could not persist generated SECRET_KEY to {secret_key_path}: {e}")

    # 2. MFA_ENCRYPTION_KEY Handling
    if not settings.mfa_encryption_key:
        mfa_key_path = Path(settings.secrets_dir) / "mfa_encryption_key"
        
        if mfa_key_path.exists():
            try:
                settings.mfa_encryption_key = mfa_key_path.read_text().strip()
                logger.info(f"MFA_ENCRYPTION_KEY loaded from {mfa_key_path}")
            except Exception as e:
                logger.error(f"Could not read MFA_ENCRYPTION_KEY from {mfa_key_path}: {e}")
        
        # Hinweis: Wir generieren hier keinen Fallback-Key, da agent/common/mfa.py 
        # bereits einen Fallback aus SECRET_KEY ableitet, wenn MFA_ENCRYPTION_KEY None ist.
        # Wenn der User jedoch eine dedizierte Datei wünscht, kann er diese nun dort ablegen.
        # Falls wir automatische Persistenz wie bei SECRET_KEY wollen:
        # if not settings.mfa_encryption_key:
        #     settings.mfa_encryption_key = secrets.token_urlsafe(32)
        #     ... persist ...
        # Da die Aufgabe sagt "implement logic similar to secret_key", 
        # aber auch erwähnt "Currently, if MFA_ENCRYPTION_KEY is not set, it derives from SECRET_KEY",
        # ist es am sichersten, es optional zu lassen, aber Dateiladen zu unterstützen.
        
except Exception as e:
    # Sicherstellen, dass wenigstens ein Basic-Logging aktiv ist
    logger = logging.getLogger("agent.config")
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    logger.error(f"Fehler beim Laden der Einstellungen: {e}", exc_info=True)
    # Minimaler Fallback falls Pydantic wegen Validierung fehlschlägt
    settings = Settings.model_construct()
