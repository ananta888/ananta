# Ananta Playwright-Tests

Es gibt drei Möglichkeiten, die E2E-Tests auszuführen:

## Testumgebung

Die Playwright-Tests setzen eine funktionierende Node.js-Umgebung (>=18) voraus. Für reproduzierbare Ergebnisse empfiehlt sich die Nutzung der bereitgestellten Docker-Container.

Stelle sicher, dass Docker installiert ist und die Ports `8081` (Controller) sowie `9444` (Playwright) frei sind.

## 1. Tests im Controller-Service ausführen

Setze die Umgebungsvariable `RUN_TESTS=true` im Controller-Service in der docker-compose.yml:

```yaml
controller:
  environment:
    - RUN_TESTS=true
    # andere Umgebungsvariablen...
```

Dann starte den Service:

```bash
docker-compose up controller
```

Die Tests werden während des Starts ausgeführt, bevor der Controller bereit ist.

## 2. Separaten Playwright-Service verwenden

Diese Option verwendet einen dedizierten Docker-Container für die Tests:

```bash
docker-compose up playwright
```

Die Tests werden ausgeführt und der Container beendet sich danach.

## 3. Tests lokal ausführen

Diese Option verwendet das Shell-Skript, um Tests direkt auf dem Host auszuführen:

```bash
chmod +x run-tests.sh
./run-tests.sh
```

**Hinweis:** Stelle sicher, dass der Controller auf Port 8081 läuft, bevor du die Tests ausführst.

## Debugging von Tests

Für die Fehlersuche bei den Tests kannst du:

1. Die Logausgabe des Containers überprüfen
2. Die Umgebungsvariable `DEBUG=pw:api` setzen, um detaillierte Playwright-Logs zu erhalten
3. Bei lokaler Ausführung mit `PWDEBUG=1` den Playwright-Inspector nutzen

## Docker-Hinweis

Sowohl der Controller- als auch der Playwright-Service basieren auf dem offiziellen Playwright-Image und bringen alle benötigten Browser mit. Nach Abschluss der Tests können Container mit `docker-compose down` entfernt werden.
