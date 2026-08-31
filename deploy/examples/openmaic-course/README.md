# Getrennte OpenMAIC-Demo

Die Compose-Datei baut den offiziellen OpenMAIC-Stand v1.0.0 am festen Commit `aa2bfb3c1d406c47100c6744d90e788abdf1f6d5`. Sie bindet die Oberfläche nur an Loopback und tritt keinem Ananta-Netzwerk bei.

```bash
cp .env.example .env.local
docker compose up --build
```

Danach `http://127.0.0.1:3000` öffnen und das Kursarchiv aus `docs/learning/courses/openmaic-ananta-codecompass/` importieren. Für reine Wiedergabe ist kein Modell erforderlich. Für eine bewusst erzeugte Variante kann eine getestete Ollama- oder OpenAI-kompatible Route ausschließlich in `.env.local` eingetragen werden.

Fallback: `docs/learning/courses/openmaic-ananta-codecompass/offline/index.html` direkt öffnen. Weder Compose noch Fallback verbinden sich mit Hub, Worker, Task Queue oder produktiven CodeCompass-Diensten.
