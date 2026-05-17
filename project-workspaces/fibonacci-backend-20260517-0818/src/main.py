from fastapi import FastAPI

# Initialisierung der FastAPI-Anwendung
app = FastAPI(
    title="API Projekt Basis",
    description="Dies ist der initiale API-Endpunkt basierend auf der Aufgabenstellung."
)

@app.get("/")
def read_root():
    """Gibt eine einfache Nachricht zurück, um die Funktionalität zu testen."""
    return {"message": "Herzlich willkommen zur API Basis! Der Service läuft erfolgreich."}

@app.get("/health")
def health_check():
    """Ermittelt den Status der API."""
    return {"status": "ok", "service": "running"}

# Hinweis: Um die Anwendung zu starten, nutzen Sie in Ihrem Terminal:
# uvicorn src.main:app --reload