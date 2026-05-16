import pytest
from src.utils import validate_input

# Unit Tests: Testen der isolierten Geschäftslogik (z.B. Validierung)
class TestUtils:

    @pytest.mark.unit
    def test_validate_input_valid_alphanumeric(self):
        """Testet einen gültigen, alphanumerischen Eingabestring."""
        assert validate_input("ValidInput123") is True

    @pytest.mark.unit
    def test_validate_input_empty_string(self):
        """Testet einen leeren String (sollte fehlschlagen)."""
        assert validate_input("") is False

    @pytest.mark.unit
    def test_validate_input_with_special_chars(self):
        """Testet einen String mit Sonderzeichen (sollte fehlschlagen)."""
        assert validate_input("Test-@#") is False

# Integration Tests: Hier würden Tests für die Interaktion von Komponenten
# oder Systemaufrufe (z.B. Datenbank-Mocking, API-Calls) stattfinden.
# @pytest.mark.integration
# def test_api_connection(self):
#     # Beispiel: Mocken eines Systemaufrufs
#     pass
