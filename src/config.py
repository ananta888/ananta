import json
import logging
import os
from typing import Dict, Any, Optional

class ConfigManager:
    """Verwaltet die Konfigurationsdatei des Systems."""

    def __init__(self, config_path: str):
        self.config_path = config_path

    def read(self) -> Dict[str, Any]:
        """Liest die Konfigurationsdatei.

        Returns:
            Die Konfiguration als Dictionary oder ein leeres Dictionary bei Fehlern
        """
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logging.error("Fehler beim Lesen der Konfiguration: %s", e)
            return {}

    def write(self, config: Dict[str, Any]) -> bool:
        """Schreibt die Konfiguration in die Datei.

        Args:
            config: Die zu speichernde Konfiguration

        Returns:
            True bei Erfolg, False bei Fehlern
        """
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            logging.error("Fehler beim Schreiben der Konfiguration: %s", e)
            return False

class LogManager:
    """Statische Klasse zur Konfiguration des Loggings."""

    @staticmethod
    def setup(name: str, level: Optional[str] = None) -> None:
        """Richtet das Logging ein.

        Args:
            name: Name des Loggers
            level: Optional, der Log-Level (INFO, DEBUG, etc.)
        """
        if level is None:
            level = os.environ.get(f"{name.upper()}_LOG_LEVEL", "INFO")

        numeric_level = getattr(logging, level.upper(), logging.INFO)
        logging.basicConfig(
            level=numeric_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
