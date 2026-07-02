# Integration-Matrix: GitHub / Jira / Slack / CI
<!-- COSMOS-020 -->

## Prinzip

Alle Integrationen laufen über neutrale Ports — keine direkte Fachkopplung zwischen Hub
und externem System. Externe Systeme bekommen keine Agentenrechte; sie senden nur
TriggerEvents oder empfangen Notifications.

Schreibaktionen (Labels, Kommentare, PRs, Merges) sind immer durch HITL-Gates abgesichert.
Lesezugriffe auf externe Systeme erfordern explizite, per Policy konfigurierte Rechte.

---

## Integrationsmatrix

| Integration              | Priorität | Datenrichtung      | Rechtebedarf    | Hauptrisiken                          | Offline-/Local-Fallback           |
|--------------------------|-----------|--------------------|-----------------|---------------------------------------|-----------------------------------|
| **Local Git**            | Must      | read + write       | low (lokal)     | Unbeabsichtigte Commits/Branches      | — (ist der Fallback)              |
| **GitHub Issues**        | Should    | read               | medium          | Datenleak bei fehlenden Repo-Rechten  | Markdown-Datei mit Issue-Content  |
| **GitHub Pull Requests** | Should    | read + write       | high            | Auto-Merge, unfreigegebene PRs        | Lokaler PR-Entwurf (pr_draft.md)  |
| **GitHub Actions / CI**  | Should    | read               | medium          | Log-Exfiltration, Secrets in Logs     | Lokaler Test-Lauf in Sandbox      |
| **Generic Webhooks**     | Later     | receive (inbound)  | medium          | Unsigned Payloads, Replay-Attacks     | Manueller Trigger via CLI         |
| **Jira Issues**          | Optional  | read               | low–medium      | Token-Scope zu weit, Datenleak        | Keiner (Integration optional)     |
| **Slack Notifications**  | Optional  | write (outbound)   | low             | Plaintext-Secrets in Nachrichten      | Lokale Log-Ausgabe                |

---

## Local Git

Basis für alle anderen Integrationen. Lese- und Schreibzugriff auf lokales Repository.

```yaml
integration: local_git
port: GitPort
operations:
  - git_status
  - git_diff
  - git_log
  - git_commit        # approval_gate: commit_to_branch
  - git_push          # approval_gate: push_to_remote
  - git_branch_create # approval_gate: create_branch
restrictions:
  - "Kein force-push auf main/master ohne owner-Approval"
  - "Kein Commit in geschützte Pfade ohne policy.allow"
```

---

## GitHub Issues

```yaml
integration: github_issues
port: GitHubIssuePort
operations:
  - list_issues       # read, kein Gate
  - read_issue        # read, kein Gate
  - add_label         # write, approval_gate: github_write
  - post_comment      # write, approval_gate: github_write
auth: github_token (scope: issues:read, issues:write wenn Schreiben aktiv)
fallback: "Issues werden als Markdown exportiert (issues/<id>.md)"
```

---

## GitHub Pull Requests

```yaml
integration: github_pull_requests
port: GitHubPRPort
operations:
  - read_pr           # read, kein Gate
  - list_pr_files     # read, kein Gate
  - read_ci_status    # read, kein Gate
  - create_draft_pr   # write, approval_gate: create_pull_request
  - post_review       # write, approval_gate: github_write
  - merge_pr          # write, approval_gate: merge_pull_request (owner only)
auth: github_token (scope: pull_requests:write wenn aktiv)
fallback: "Lokaler PR-Entwurf als pr_draft.md gespeichert"
note: "merge_pr ist Default-Deny; muss explizit in Policy aktiviert werden"
```

---

## GitHub Actions / CI

```yaml
integration: github_ci
port: CIPort
operations:
  - read_check_runs   # read, kein Gate
  - read_ci_log       # read, approval_gate: read_ci_log (Logs können Secrets enthalten)
  - rerun_workflow    # write, approval_gate: rerun_ci
auth: github_token (scope: checks:read, actions:read)
fallback: "Lokaler Test-Lauf in Sandbox (ohne CI-Artefakte)"
note: "CI-Logs werden gefiltert: bekannte Secret-Patterns werden redigiert"
```

---

## Generic Webhooks (inbound)

```yaml
integration: webhooks
port: WebhookReceiverPort
operations:
  - receive_event     # Webhook → TriggerEvent → Hub
validation:
  - HMAC-Signatur prüfen (konfigurierbar, Default: required)
  - Rate-Limit: 10 Events/Minute pro Source (konfigurierbar)
  - Payload-Schema muss registriertem TriggerEventType entsprechen
fallback: "Manueller Trigger via CLI (ananta run --goal ...)"
```

---

## Jira Issues

```yaml
integration: jira
port: IssueTrackerPort    # selbe Abstraktion wie GitHubIssuePort
operations:
  - list_issues
  - read_issue
auth: jira_api_token (scope: read:jira-work)
status: optional — nicht vorinstalliert
fallback: "Keine (Integration ist vollständig optional)"
note: "Jira-Schreiboperationen (Statuswechsel, Kommentare) sind separater Scope"
```

---

## Slack Notifications

```yaml
integration: slack
port: NotificationPort
operations:
  - send_message      # outbound only, approval_gate: send_external_notification
auth: slack_bot_token (scope: chat:write)
content_rules:
  - "Keine Artefakt-Inhalte inline — nur Links und Zusammenfassungen"
  - "Kein Secret-Plaintext; nur Refs"
fallback: "Ausgabe in lokales Log (stdout/notification.jsonl)"
status: optional
```

---

## Schreibaktionen und Gates

Alle Schreibaktionen auf externe Systeme erfordern ein Approval-Gate:

| Aktion                | Gate-Typ                      | Mindestrolle  |
|-----------------------|-------------------------------|---------------|
| GitHub Draft PR       | `create_pull_request`         | operator      |
| GitHub Review posten  | `github_write`                | reviewer      |
| GitHub Label setzen   | `github_write`                | operator      |
| GitHub Merge          | `merge_pull_request`          | maintainer    |
| CI Workflow neu starten | `rerun_ci`                  | operator      |
| Slack Nachricht senden | `send_external_notification` | operator      |
| Git Commit            | `commit_to_branch`            | operator      |
| Git Push              | `push_to_remote`              | operator      |

---

## Tests

| Testfall                                         | Erwartung                                           |
|--------------------------------------------------|-----------------------------------------------------|
| GitHub nicht konfiguriert, PR-Entwurf angefordert | pr_draft.md erzeugt, kein GitHub-Aufruf            |
| Webhook ohne HMAC-Signatur                       | Payload abgelehnt, Audit-Event                      |
| CI-Log-Abruf ohne Approval                       | 403, Gate blockiert                                 |
| Merge ohne maintainer-Rolle                      | 403, Gate blockiert                                 |
| Slack-Nachricht mit Secret-Ref in Artefakt       | Nachricht enthält nur Ref, nicht Plaintext           |
