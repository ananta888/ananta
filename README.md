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
