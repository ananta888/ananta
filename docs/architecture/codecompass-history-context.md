# CodeCompass History Context aus Git, Issues und PRs (COSMOS-011)

## Ziel

Commit-History, Pull Requests, Reviews, Issues und Architekturentscheidungen (ADRs) werden
als zusätzliche Kontextsignale nutzbar — ohne aktuelle Code-Evidenz zu ersetzen. History ist
ein ergänzendes Signal mit explizitem Freshness-Tracking, kein primärer Wahrheitsanker.

---

## HistoryProvider-Port

```python
from typing import Protocol

class HistoryProvider(Protocol):
    def get_commits(self, paths: list[str], limit: int) -> list[CommitRecord]: ...
    def get_prs(self, paths: list[str], limit: int) -> list[PRRecord]: ...
    def get_issues(self, keywords: list[str], limit: int) -> list[IssueRecord]: ...
    def get_adrs(self, query: str) -> list[ADRRecord]: ...
```

Implementierungen:
- `LocalGitHistoryProvider` — liest lokale Git-History (default)
- `GitHubHistoryProvider` — liest PRs, Issues, Reviews über GitHub API (optional)
- `NullHistoryProvider` — deaktivierter Provider, gibt leere Listen zurück
- `FakeHistoryProvider` — für Tests, fest verdrahtete Fixture-Daten

Der aktive Provider wird pro Projekt in `codecompass/config/history.yaml` konfiguriert.

---

## Record-Schemata

```python
@dataclass
class CommitRecord:
    commit_id: str
    author: str
    timestamp: datetime
    message: str
    changed_paths: list[str]
    confidence: float = 1.0        # lokale Git-History ist deterministisch

@dataclass
class PRRecord:
    pr_id: str
    title: str
    author: str
    merged_at: datetime | None
    changed_paths: list[str]
    description: str
    review_comments: list[str]
    confidence: float = 0.9        # API-Daten, leicht unsicherer als lokales Git

@dataclass
class IssueRecord:
    issue_id: str
    title: str
    body: str
    created_at: datetime
    closed_at: datetime | None
    labels: list[str]
    confidence: float = 0.7        # Issues beschreiben Kontext, nicht Code-Wahrheit

@dataclass
class ADRRecord:
    adr_id: str
    title: str
    status: Literal["proposed", "accepted", "deprecated", "superseded"]
    content: str
    created_at: datetime
    confidence: float = 0.8
```

---

## Freshness-Regel

History-Treffer älter als ein konfigurierbarer Schwellwert (`history_stale_days`, default: 180)
werden als `stale` markiert:

```python
@dataclass
class HistoryHit:
    record: CommitRecord | PRRecord | IssueRecord | ADRRecord
    freshness: float    # 1.0 = heute, 0.0 = sehr alt (linear abfallend)
    is_stale: bool      # True wenn älter als history_stale_days
    stale_reason: str | None
```

Stale-Treffer:
- Erhalten in der Context Curation Pipeline einen Abzug beim Freshness-Ranking.
- Werden **nicht** stumm verworfen — erscheinen mit `[STALE]`-Markierung im Output.
- Werden nicht höher bewertet als aktueller Code (Freshness-Score aktueller Code > stale History).

---

## Berechtigungen

| Szenario                              | Verhalten                                              |
|---------------------------------------|--------------------------------------------------------|
| Lokale Git-History, kein Token nötig  | Immer erlaubt (Default)                               |
| GitHub API ohne Token                 | Blockiert mit klarer Fehlermeldung, kein stiller Fall |
| GitHub API mit Token, privates Repo   | Nur wenn explizit konfiguriert und Token in Policy    |
| Fremdes Repo ohne Zugriff             | `HistoryProvider` gibt leere Liste + Fehler-Log zurück |

Kein stiller Fallback: wenn ein konfigurierter Provider nicht erreichbar ist, wird dies
im ContextTrace protokolliert. Der Request schlägt **nicht** stillschweigend auf den
`NullHistoryProvider` um.

---

## Deaktivierung

```yaml
# codecompass/config/history.yaml
history:
  enabled: false              # History komplett deaktivieren
  provider: local_git         # "local_git" | "github" | "null"
  stale_days: 180
  max_commits_per_query: 20
  max_prs_per_query: 10
```

Default: nur lokale Git-History aktiv, GitHub-Provider deaktiviert.

---

## Tests

| Test                              | Beschreibung                                                        |
|-----------------------------------|---------------------------------------------------------------------|
| `test_git_commit_fixture`         | Fixture-Repo mit bekannten Commits → get_commits gibt korrekte Liste |
| `test_stale_commit_marked`        | Commit älter als stale_days → is_stale=True, freshness < 0.2       |
| `test_stale_not_higher_than_code` | Stale History-Treffer hat niedrigeren Score als aktueller Code-Treffer |
| `test_missing_github_token`       | GitHub-Provider ohne Token → klarer Fehler, kein stiller Fallback  |
| `test_disabled_provider`          | history.enabled=false → leere Listen, kein Netzwerkzugriff         |
| `test_pr_record_schema`           | PRRecord enthält alle Pflichtfelder und confidence >= 0.0           |
