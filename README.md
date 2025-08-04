# ananta

Simple Python controllers and agent for testing.

Der Controller stellt nun eine Konfiguration bereit, mit der zwischen
verschiedenen Modell-Anbietern gewählt werden kann. Unterstützt werden
``ollama``, ``lmstudio`` und die ``openai`` API. Der gewählte Anbieter wird in
der Konfigurationsdatei als Feld ``provider`` gespeichert und vom Agenten
entsprechend verwendet.

## Entwicklung

Installiere Abhängigkeiten und führe Tests aus:

```bash
pip install -r requirements.txt
pytest
```

### Vue Frontend

Eine experimentelle Weboberfläche auf Basis von [Vue 3](https://vuejs.org/) befindet sich im Verzeichnis `frontend`.

```bash
cd frontend
npm install
npm run dev   # Entwicklungsserver auf http://localhost:5173
npm run build # erzeugt statische Dateien für den Flask-Server
```

Nach dem Build können die Dateien über den Flask-Controller unter `/ui` abgerufen werden.

## Multi-Agent Standardkonfiguration

Die Datei `default_team_config.json` liefert eine Vorlage für ein mehrstufiges Agententeam
inklusive Rollenbeschreibung, bevorzugter Hardware und Beispiel-Prompt-Templates.
Sie kann als Ausgangspunkt genutzt werden, um ein Team aus Architekt, Backend-,
Frontend-Entwicklern und weiteren Rollen in `ai_agent.py` zu orchestrieren.
