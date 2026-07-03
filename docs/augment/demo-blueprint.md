# Augment Integration — End-to-End Demo Blueprint

## Übersicht

Dieser Blueprint zeigt den vollständigen Flow einer Augment-Integration:
Query → ContextProvider → Worker → Audit → Ergebnis.

Der Demo läuft **ohne Augment-Account** im Fake-Modus. Mit echter Auggie CLI kann er optional live ausgeführt werden.

---

## Demo-Flow

```
User Query
    ↓
ContextProvider (fake oder Augment MCP)
    ↓
Plan + Context Bundle
    ↓
AuggieCliWorker oder ProviderRouter (fake oder live)
    ↓
ChangeProposal oder ReadResult
    ↓
AuditReport + Metrics
    ↓
Output an Nutzer
```

---

## Fake-Modus (kein Augment-Account benötigt)

```python
from agent.services.augment.augment_config import AugmentConfig
from agent.services.context_providers.context_provider_port import FakeContextProvider, ContextItem

# 1. Fake-Provider konfigurieren
fake_items = [
    ContextItem(item_id="1", provider="fake", path="src/auth.py",
                snippet="def authenticate(user): ...", score=0.9),
    ContextItem(item_id="2", provider="fake", path="src/user.py",
                snippet="class User: ...", score=0.8),
]
fake_provider = FakeContextProvider(items=fake_items)

# 2. Query ausführen
result = fake_provider.retrieve("find authentication code")
print(f"Retrieved {len(result.items)} items")
for item in result.items:
    print(f"  [{item.score:.2f}] {item.path}")

# 3. Metrics
from agent.services.augment.augment_metrics import AugmentMetricsCollector, ProviderLabel
metrics = AugmentMetricsCollector()
metrics.record(provider=ProviderLabel.FAKE, operation="retrieve",
               latency_ms=10, items_returned=len(result.items))
print(metrics.compare_providers(query_used="find authentication code").to_markdown())
```

---

## Live-Modus (mit Auggie CLI)

```python
import shutil
if not shutil.which("auggie"):
    print("auggie not installed — run in fake mode instead")
    exit(0)

from agent.services.augment.augment_config import AugmentConfig
from agent.services.augment.augment_healthcheck import AugmentHealthcheck
from agent.services.augment.augment_context_provider import AugmentContextProvider

cfg = AugmentConfig()
cfg.mcp.enabled = True

health = AugmentHealthcheck().run()
provider = AugmentContextProvider(config=cfg, health_status=health)

if not provider.is_enabled():
    print(f"Provider not ready: {health.overall}")
    exit(1)

from agent.services.context_providers.context_provider_port import ContextScope
scope = ContextScope(workspace_id="demo", allowed_paths=["src/"], denied_paths=[".env"])
result = provider.retrieve("find authentication code", scope=scope)
print(f"Retrieved {len(result.items)} items from Augment MCP")
```

---

## Demo ausführen

```bash
# Fake-Modus (kein Augment benötigt)
python -c "
from agent.services.context_providers.context_provider_port import FakeContextProvider, ContextItem
from agent.services.augment.augment_metrics import AugmentMetricsCollector, ProviderLabel

items = [ContextItem(item_id='1', provider='fake', path='src/main.py', snippet='def main(): pass', score=0.8)]
p = FakeContextProvider(items=items)
r = p.retrieve('find main')
m = AugmentMetricsCollector()
m.record(provider=ProviderLabel.FAKE, operation='retrieve', latency_ms=5, items_returned=len(r.items))
print('Demo OK:', len(r.items), 'items retrieved')
print(m.compare_providers().to_markdown())
"

# Live-Modus (mit Auggie)
# python demo/run_live_demo.py  # erfordert: auggie login
```

---

## Ausgabe-Beispiel (Fake-Modus)

```
Demo OK: 1 items retrieved
# Provider Comparison

| Provider | Ops | Avg Latency | Cost | Items/call | Error Rate |
|---|---|---|---|---|---|
| fake | 1 | 5ms | 0.0000 | 1.0 | 0.0% |

**Fastest:** fake
**Cheapest:** fake
```
