# Docker auf Windows: Hot-Reload Workaround

Bei der Entwicklung mit Docker Desktop auf Windows kann es zu Problemen mit dem "Hot-Reload" von Volumes kommen, insbesondere bei Frontend-Frameworks wie Angular.

## Das Problem

Aenderungen am Quellcode auf dem Host-System werden zwar in den Container gespiegelt (Volume Mount), aber der Build-Prozess innerhalb des Containers (z.B. `ng serve`) erkennt die Dateiaenderungen nicht zuverlaessig oder liefert weiterhin alte, gecachte JavaScript-Bundles aus.

Dies fuehrt dazu, dass im Browser trotz Code-Aenderungen die alte Version der Anwendung angezeigt wird oder Tests gegen einen veralteten Stand laufen.

## Die Loesung: Vollstaendiger Rebuild

Der zuverlaessigste Weg, um sicherzustellen, dass alle Aenderungen uebernommen werden, ist ein Neustart der Container mit einem vollstaendigen Rebuild ohne Cache.

### Befehl
```bash
docker compose up -d --build
```

Oder noch gruendlicher:
```bash
docker compose down -v --remove-orphans
docker compose up -d --build
```

Wenn unter Windows Fehler wie `invalid volume specification` auftreten:
```powershell
$env:COMPOSE_CONVERT_WINDOWS_PATHS=1
docker compose up -d --build
```

## Best Practices fuer die Entwicklung

1. **Manueller Build vor Start**: Fuehren Sie `npm run build` auf dem Host aus, bevor Sie die Container starten, wenn Sie nicht den Dev-Server im Container nutzen.
2. **Browser-Cache leeren**: Oft hilft es auch, den Browser-Cache zu leeren oder ein privates Fenster zu nutzen, falls das Frontend bereits neu gebaut wurde, der Browser aber noch alte Files haelt.
3. **CI-Pipeline**: In Continuous Integration Umgebungen (wie GitHub Actions) sollte immer mit dem Flag `--no-cache` gebaut werden, um Seiteneffekte zu vermeiden.

## Troubleshooting

Falls der Agent keine Verbindung zum LLM auf dem Host aufbauen kann (z.B. Ollama oder LM Studio), nutzen Sie das Skript:
```powershell
.\setup_host_services.ps1
```
Dieses Skript konfiguriert die Firewall und den Proxy fuer den Zugriff auf `host.docker.internal`.

Falls Redis beim Start `vm.overcommit_memory` meldet, setzen Sie den Wert auf dem Host einmalig (Admin-PowerShell):
```powershell
wsl -d docker-desktop sysctl -w vm.overcommit_memory=1
```
Persistente Variante (bleibt nach Neustarts erhalten):
```powershell
wsl -d docker-desktop sh -c "echo 'vm.overcommit_memory=1' >> /etc/sysctl.conf && sysctl -p"
```
