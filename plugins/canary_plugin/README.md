# Canary Plugin

Dieses Plugin dient als Referenz-Implementierung für Ananta Evolution-Provider.

## Nutzung

Aktivieren Sie das Plugin über die Umgebungsvariablen:

```bash
AGENT_PLUGIN_DIRS=plugins
AGENT_PLUGINS=canary_plugin
```

## Zweck

Es wird in automatisierten Tests verwendet, um sicherzustellen, dass die Plugin-Schnittstellen (SDK)
stabil bleiben und korrekt funktionieren.
