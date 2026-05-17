from fastapi.testclient import TestClient
from fib_app.main import app

client = TestClient(app)

def test_read_root():
    """Testet den Basis-Endpoint, um sicherzustellen, dass die API startet."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"service": "Running", "description": "Minimal API structure initialized."}

# Weitere Testfälle kommen hierher...
