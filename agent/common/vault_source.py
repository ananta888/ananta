import logging
import os
from typing import Any, Dict

from pydantic_settings import PydanticBaseSettingsSource

try:
    import hvac
except ImportError:
    hvac = None

class VaultSettingsSource(PydanticBaseSettingsSource):
    """
    Eine Pydantic Settings Source, die Secrets aus HashiCorp Vault lädt.
    """
    def __call__(self) -> Dict[str, Any]:
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

            read_response = client.secrets.kv.v2.read_secret_version(
                mount_point=mount_point,
                path=vault_path
            )

            return read_response['data']['data']
        except Exception as e:
            logging.error(f"Error loading secrets from Vault: {e}")
            return {}
