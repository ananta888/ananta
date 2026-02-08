# Docker auf Windows: Hot-Reload Workaround

Bei der Entwicklung mit Docker Desktop auf Windows kann es zu Problemen mit dem "Hot-Reload" von Volumes kommen, insbesondere bei Frontend-Frameworks wie Angular.

## Das Problem

Änderungen am Quellcode auf dem Host-System werden zwar in den Container gespiegelt (Volume Mount), aber der Build-Prozess innerhalb des Containers (z.B. `ng serve`) erkennt die Dateiänderungen nicht zuverlässig oder liefert weiterhin alte, gecachte JavaScript-Bundles aus.

Dies führt dazu, dass im Browser trotz Code-Änderungen die alte Version der Anwendung angezeigt wird oder Tests gegen einen veralteten Stand laufen.

## Die Lösung: Vollständiger Rebuild

Der zuverlässigste Weg, um sicherzustellen, dass alle Änderungen übernommen werden, ist ein Neustart der Container mit einem vollständigen Rebuild ohne Cache.

### Befehl
```bash
docker-compose up -d --build
```

Oder noch gründlicher:
```bash
docker-compose down
docker-compose up -d --build
```

## Best Practices für die Entwicklung

1. **Manueller Build vor Start**: Führen Sie `npm run build` auf dem Host aus, bevor Sie die Container starten, wenn Sie nicht den Dev-Server im Container nutzen.
2. **Browser-Cache leeren**: Oft hilft es auch, den Browser-Cache zu leeren oder ein privates Fenster zu nutzen, falls das Frontend bereits neu gebaut wurde, der Browser aber noch alte Files hält.
3. **CI-Pipeline**: In Continuous Integration Umgebungen (wie GitHub Actions) sollte immer mit dem Flag `--no-cache` gebaut werden, um Seiteneffekte zu vermeiden.

## Troubleshooting

Falls der Agent keine Verbindung zum LLM auf dem Host aufbauen kann (z.B. Ollama oder LM Studio), nutzen Sie das Skript:
```powershell
.\setup_host_services.ps1
```
Dieses Skript konfiguriert die Firewall und den Proxy für den Zugriff auf `host.docker.internal`.
