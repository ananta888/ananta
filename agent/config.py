
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
import time
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
    lmstudio_url: str = Field(default="http://host.docker.internal:1234/v1", validation_alias="LMSTUDIO_URL")
    lmstudio_api_mode: str = Field(default="chat", validation_alias="LMSTUDIO_API_MODE")
    openai_url: str = Field(default="https://api.openai.com/v1/chat/completions", validation_alias="OPENAI_URL")
    anthropic_url: str = Field(default="https://api.anthropic.com/v1/messages", validation_alias="ANTHROPIC_URL")
    mock_url: str = Field(default="http://mock-llm/v1", validation_alias="MOCK_URL")
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    
    # LLM Defaults
    default_provider: str = Field(default="lmstudio", validation_alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="auto", validation_alias="DEFAULT_MODEL")
    lmstudio_max_context_tokens: int = Field(default=4096, validation_alias="LMSTUDIO_MAX_CONTEXT_TOKENS")
    
    # Logging
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool = Field(default=False, validation_alias="AI_AGENT_LOG_JSON")
    
    # Shell
    shell_path: Optional[str] = Field(default=None, validation_alias="SHELL_PATH")
    shell_pool_size: int = Field(default=5, validation_alias="SHELL_POOL_SIZE")
    
    # Timeouts
    http_timeout: int = Field(default=60, validation_alias="HTTP_TIMEOUT")
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
    vault_url: Optional[str] = Field(default=None, validation_alias="VAULT_URL")
    vault_token: Optional[str] = Field(default=None, validation_alias="VAULT_TOKEN")
    vault_mount_point: str = Field(default="secret", validation_alias="VAULT_MOUNT_POINT")
    vault_path: str = Field(default="ananta", validation_alias="VAULT_PATH")
    
    secret_key: str = Field(default="", validation_alias="SECRET_KEY")
    mfa_encryption_key: Optional[str] = Field(default=None, validation_alias="MFA_ENCRYPTION_KEY")
    agent_token_persistence: bool = Field(default=True, validation_alias="AGENT_TOKEN_PERSISTENCE")
    cors_origins: str = Field(default="*", validation_alias="CORS_ORIGINS")
    registration_token: Optional[str] = Field(default=None, validation_alias="REGISTRATION_TOKEN")
    token_rotation_days: int = Field(default=7, validation_alias="TOKEN_ROTATION_DAYS")
    auto_update_dotenv: bool = Field(default=False, validation_alias="AUTO_UPDATE_DOTENV")
    enable_advanced_command_analysis: bool = Field(default=False, validation_alias="ENABLE_ADVANCED_COMMAND_ANALYSIS")
    fail_secure_llm_analysis: bool = Field(default=False, validation_alias="FAIL_SECURE_LLM_ANALYSIS")
    disable_llm_check: bool = Field(default=False, validation_alias="DISABLE_LLM_CHECK")
    
    # Initial User
    initial_admin_user: str = Field(default="admin", validation_alias="INITIAL_ADMIN_USER")
    initial_admin_password: Optional[str] = Field(default=None, validation_alias="INITIAL_ADMIN_PASSWORD")
    disable_initial_admin: bool = Field(default=False, validation_alias="DISABLE_INITIAL_ADMIN")
    
    # SGPT Config
    sgpt_default_model: str = Field(default="gpt-4o", validation_alias="SGPT_DEFAULT_MODEL")
    sgpt_default_color: str = Field(default="magenta", validation_alias="SGPT_DEFAULT_COLOR")
    sgpt_code_theme: str = Field(default="dracula", validation_alias="SGPT_CODE_THEME")
    sgpt_prettify_markdown: bool = Field(default=True, validation_alias="SGPT_PRETTIFY_MARKDOWN")
    sgpt_use_litellm: bool = Field(default=False, validation_alias="SGPT_USE_LITELLM")
    sgpt_shell_interaction: bool = Field(default=True, validation_alias="SGPT_SHELL_INTERACTION")
    sgpt_execution_backend: str = Field(default="sgpt", validation_alias="SGPT_EXECUTION_BACKEND")
    opencode_path: str = Field(default="opencode", validation_alias="OPENCODE_PATH")
    opencode_default_model: Optional[str] = Field(default=None, validation_alias="OPENCODE_DEFAULT_MODEL")
    aider_path: str = Field(default="aider", validation_alias="AIDER_PATH")
    aider_default_model: Optional[str] = Field(default=None, validation_alias="AIDER_DEFAULT_MODEL")
    mistral_code_path: str = Field(default="mistral-code", validation_alias="MISTRAL_CODE_PATH")
    mistral_code_default_model: Optional[str] = Field(default=None, validation_alias="MISTRAL_CODE_DEFAULT_MODEL")

    # Hybrid RAG Config
    rag_enabled: bool = Field(default=True, validation_alias="RAG_ENABLED")
    rag_repo_root: str = Field(default=".", validation_alias="RAG_REPO_ROOT")
    rag_data_roots: str = Field(default="docs,data", validation_alias="RAG_DATA_ROOTS")
    rag_max_context_chars: int = Field(default=12000, validation_alias="RAG_MAX_CONTEXT_CHARS")
    rag_max_context_tokens: int = Field(default=3000, validation_alias="RAG_MAX_CONTEXT_TOKENS")
    rag_max_chunks: int = Field(default=12, validation_alias="RAG_MAX_CHUNKS")
    rag_agentic_max_commands: int = Field(default=3, validation_alias="RAG_AGENTIC_MAX_COMMANDS")
    rag_agentic_timeout_seconds: int = Field(default=8, validation_alias="RAG_AGENTIC_TIMEOUT_SECONDS")
    rag_semantic_persist_dir: str = Field(default=".rag/llamaindex", validation_alias="RAG_SEMANTIC_PERSIST_DIR")
    rag_redact_sensitive: bool = Field(default=True, validation_alias="RAG_REDACT_SENSITIVE")
    
    # Database
    database_url: Optional[str] = Field(default=None, validation_alias="DATABASE_URL")

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = os.path.join(self.data_dir, "ananta.db")
        return f"sqlite:///{db_path}"
    
    @property
    def token_path(self) -> str:
        return os.path.join(self.secrets_dir, "agent_token.json")

    def save_agent_token(self, token: str) -> None:
        """Persistiert den Agent Token sicher."""
        self.agent_token = token
        if not self.agent_token_persistence:
            return
            
        try:
            os.makedirs(self.secrets_dir, exist_ok=True)
            path = self.token_path
            # Wir nutzen hier direkt json.dump um Abhängigkeiten zu minimieren, 
            # aber halten uns an das Format von write_json (indent=2)
            data = {
                "agent_token": token,
                "last_rotation": time.time()
            }
            
            # Restriktive Berechtigungen falls Datei neu
            if not os.path.exists(path):
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
                    os.close(fd)
                except Exception:
                    pass
            
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            
            # Optional: .env Datei aktualisieren
            if self.auto_update_dotenv:
                self._update_dotenv("AGENT_TOKEN", token)
            
            logging.getLogger("agent.config").info(f"Agent Token erfolgreich in {path} persistiert.")
        except Exception as e:
            logging.getLogger("agent.config").error(f"Fehler beim Persistieren des Agent Tokens: {e}")

    def _update_dotenv(self, key: str, value: str) -> None:
        """Aktualisiert einen Wert in der .env Datei."""
        dotenv_path = ".env"
        if not os.path.exists(dotenv_path):
            return
            
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            updated = False
            new_lines = []
            for line in lines:
                if line.startswith(f"{key}=") or line.startswith(f'export {key}='):
                    new_lines.append(f"{key}={value}\n")
                    updated = True
                else:
                    new_lines.append(line)
            
            if not updated:
                new_lines.append(f"{key}={value}\n")
                
            with open(dotenv_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            logging.getLogger("agent.config").info(f"{key} in .env aktualisiert.")
        except Exception as e:
            logging.getLogger("agent.config").error(f"Fehler beim Aktualisieren der .env: {e}")

    # Redis
    redis_url: Optional[str] = Field(default=None, validation_alias="REDIS_URL")
    
    # Task Archiving
    tasks_retention_days: int = Field(default=30, validation_alias="TASKS_RETENTION_DAYS")
    archived_tasks_retention_days: int = Field(default=90, validation_alias="ARCHIVED_TASKS_RETENTION_DAYS")
    backups_retention_days: int = Field(default=14, validation_alias="BACKUPS_RETENTION_DAYS")
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

    @field_validator("sgpt_execution_backend")
    @classmethod
    def validate_sgpt_execution_backend(cls, v: str) -> str:
        allowed = {"sgpt", "opencode", "aider", "mistral_code", "auto"}
        val = (v or "").strip().lower()
        if val not in allowed:
            raise ValueError(f"SGPT_EXECUTION_BACKEND muss einer der folgenden Werte sein: {sorted(allowed)}")
        return val

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
        
        # Vault Source hinzufügen, wenn konfiguriert
        vault_url = os.getenv("VAULT_URL")
        vault_token = os.getenv("VAULT_TOKEN")
        if vault_url and vault_token:
            try:
                from agent.common.vault_source import VaultSettingsSource
                sources.append(VaultSettingsSource(settings_cls))
            except ImportError:
                logging.getLogger("agent.config").warning("hvac not installed, skipping VaultSettingsSource")

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

    # 3. AGENT_TOKEN Handling (Migration and Loading)
    token_path = Path(settings.token_path)
    # Migration von altem Pfad falls nötig (data/token.json -> secrets/agent_token.json)
    old_token_path = Path(settings.data_dir) / "token.json"
    if old_token_path.exists() and not token_path.exists():
        try:
            os.makedirs(settings.secrets_dir, exist_ok=True)
            import shutil
            shutil.move(str(old_token_path), str(token_path))
            logger.info(f"Migrated agent token from {old_token_path} to {token_path}")
        except Exception as e:
            logger.error(f"Failed to migrate agent token: {e}")

    if token_path.exists():
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                token_data = json.load(f)
                if not settings.agent_token: # Nur laden, wenn nicht bereits via ENV gesetzt
                    settings.agent_token = token_data.get("agent_token")
                    if settings.agent_token:
                        logger.info(f"Agent token loaded from {token_path}")
        except Exception as e:
            logger.error(f"Could not read agent token from {token_path}: {e}")
        
except Exception as e:
    # Sicherstellen, dass wenigstens ein Basic-Logging aktiv ist
    logger = logging.getLogger("agent.config")
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    logger.error(f"Fehler beim Laden der Einstellungen: {e}", exc_info=True)
    # Minimaler Fallback falls Pydantic wegen Validierung fehlschlägt
    settings = Settings.model_construct()
