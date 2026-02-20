import logging
import os
from typing import Any, Dict, Tuple, Type

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

try:
    import hvac
except ImportError:
    hvac = None


class VaultSettingsSource(PydanticBaseSettingsSource):
    """
    Eine Pydantic Settings Source, die Secrets aus HashiCorp Vault lädt.
    """

    def __init__(self, settings_cls: Type[BaseSettings]):  # type: ignore[override]
        super().__init__(settings_cls)
        self._secrets: Dict[str, Any] = {}
        self._loaded = False

    def _load_secrets(self) -> Dict[str, Any]:
        """Load secrets from Vault once."""
        if self._loaded:
            return self._secrets

        self._loaded = True

        if not hvac:
            return {}

        vault_url = os.getenv("VAULT_URL")
        vault_token = os.getenv("VAULT_TOKEN")
        mount_point = os.getenv("VAULT_MOUNT_POINT", "secret")
        vault_path = os.getenv("VAULT_PATH", "ananta")

        if not vault_url or not vault_token:
            return {}

        try:
            client = hvac.Client(url=vault_url, token=vault_token)
            if not client.is_authenticated():
                logging.error("Vault authentication failed.")
                return {}

            read_response = client.secrets.kv.v2.read_secret_version(mount_point=mount_point, path=vault_path)
            self._secrets = read_response["data"]["data"]
            return self._secrets
        except Exception as e:
            logging.error(f"Error loading secrets from Vault: {e}")
            return {}

    def get_field_value(self, field: Any, field_name: str) -> Tuple[Any, str, bool]:
        """Get value for a specific field from Vault."""
        secrets = self._load_secrets()
        if field_name in secrets:
            return secrets[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> Dict[str, Any]:
        """Return all secrets for settings initialization."""
        return self._load_secrets()
