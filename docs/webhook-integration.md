# Webhook-Integration Guide

## Übersicht

Das Trigger-System ermöglicht die automatische Task-Erstellung aus externen Quellen via Webhooks.

## Unterstützte Quellen

| Source | Beschreibung | Payload-Format |
|--------|--------------|----------------|
| `generic` | Allgemeine JSON-Webhooks | `{title, description, priority, tasks[]}` |
| `github` | GitHub Issues & PRs | GitHub Webhook Format |
| `slack` | Slack Events | Slack Event API Format |
| `jira` | Jira Webhooks | Jira Webhook Format |

## Webhook-URLs

```
http://<hub-url>:5000/triggers/webhook/<source>
```

Beispiele:
- Generic: `http://localhost:5000/triggers/webhook/generic`
- GitHub: `http://localhost:5000/triggers/webhook/github`
- Slack: `http://localhost:5000/triggers/webhook/slack`
- Jira: `http://localhost:5000/triggers/webhook/jira`

## GitHub Integration

### Einrichtung

1. Öffne Repository → Settings → Webhooks → Add webhook
2. Payload URL: `http://<your-hub>:5000/triggers/webhook/github`
3. Content type: `application/json`
4. Secret: (optional) dein konfiguriertes Secret
5. Events: Issues, Pull requests

### Erkannte Events

| Event | Aktion |
|-------|--------|
| `issues.opened` | Erstellt Task mit Issue-Titel |
| `issues.reopened` | Erstellt Task |
| `issues.labeled` | Erstellt Task (Priority: High bei Bug-Label) |
| `pull_request.opened` | Erstellt PR-Review Task |

### Beispiel-Payload (Issue)
```json
{
  "action": "opened",
  "issue": {
    "number": 42,
    "title": "Bug in login",
    "body": "Description...",
    "html_url": "https://github.com/org/repo/issues/42"
  },
  "repository": {
    "full_name": "org/repo"
  }
}
```

## Slack Integration

### Einrichtung

1. Erstelle eine Slack App
2. Aktiviere Events
3. Request URL: `http://<your-hub>:5000/triggers/webhook/slack`
4. Abonniere `message.channels` Events

### Erkannte Events

- `message.channels`: Erstellt Task aus Channel-Nachricht

## Jira Integration

### Einrichtung

1. Jira → Settings → Webhooks
2. URL: `http://<your-hub>:5000/triggers/webhook/jira`
3. Events: issue.created, issue.updated

### Erkannte Events

| Event | Aktion |
|-------|--------|
| `jira:issue_created` | Erstellt Task |
| `jira:issue_updated` | Aktualisiert Task |

## Generic Webhook

Für eigene Integrationen:

```bash
curl -X POST http://localhost:5000/triggers/webhook/generic \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Manueller Task",
    "description": "Beschreibung hier",
    "priority": "High"
  }'
```

### Mehrere Tasks auf einmal
```json
{
  "tasks": [
    {"title": "Task 1", "description": "...", "priority": "High"},
    {"title": "Task 2", "description": "...", "priority": "Medium"}
  ]
}
```

## Sicherheit

### Webhook-Secrets

Konfiguriere Secrets für Signatur-Validierung:

```bash
POST /triggers/configure
{
  "webhook_secrets": {
    "github": "dein-geheimes-secret",
    "slack": "slack-signing-secret"
  }
}
```

### IP-Whitelist

Beschränke Webhooks auf bestimmte IPs:

```bash
POST /triggers/configure
{
  "ip_whitelists": {
    "github": ["192.30.252.0/22"],
    "jira": ["52.202.0.0/16"]
  }
}
```

### Rate Limiting

Schutz vor Überlastung:

```bash
POST /triggers/configure
{
  "rate_limits": {
    "generic": {"max_requests": 60, "window_seconds": 60},
    "github": {"max_requests": 100, "window_seconds": 60}
  }
}
```

## Frontend

Die Webhook-Konfiguration ist unter `/webhooks` erreichbar.

## Testing

Teste Webhooks ohne Task-Erstellung:

```bash
POST /triggers/test
{
  "source": "generic",
  "payload": {"title": "Test"}
}
```

## Automatischer Ablauf

```
1. Webhook empfängt Payload
2. Signatur wird validiert (falls konfiguriert)
3. IP wird geprüft (falls Whitelist aktiv)
4. Rate Limit wird geprüft
5. Handler verarbeitet Payload
6. Tasks werden erstellt
7. Autopilot startet (falls konfiguriert)
```
