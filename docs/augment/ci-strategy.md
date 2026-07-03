# CI-Strategie ohne Augment-Account

## Grundprinzip

Die Test-Suite läuft vollständig ohne echte Auggie-Installation oder Augment-Account.
Alle Augment-Integrationen sind hinter Feature-Flags und nutzen Fake-Provider in Tests.

## Test-Marker

```python
# Augment-abhängige Tests markieren:
import pytest

@pytest.mark.auggie_live  # erfordert echte auggie-Installation
def test_real_auggie_query():
    ...

@pytest.mark.skipif(
    not shutil.which("auggie"),
    reason="auggie not installed"
)
def test_auggie_version():
    ...
```

## CI-Konfiguration

```yaml
# .github/workflows/tests.yml
- name: Run unit tests (no Augment required)
  run: python -m pytest tests/ -m "not auggie_live" -q

- name: Run smoke tests (Augment available)
  if: env.AUGGIE_TOKEN != ''
  run: python -m pytest tests/ -m "auggie_live" -q
  env:
    AUGGIE_TOKEN: ${{ secrets.AUGGIE_TOKEN }}
```

## Fake-Provider für Tests

Alle Augment-Services haben Fake-Implementierungen:

- `FakeContextProvider` — injizierbare Fake-Ergebnisse
- `AugmentHealthcheck` mit gemocktem `subprocess.run`
- `AuggieCliWorker` mit gemocktem `shutil.which` → immer "not found"
- `AugmentContextProvider` mit `mcp_caller` Parameter → injizierbarer Fake

## Grüner CI ohne Auggie-Installation

Alle Unit-Tests passen in `tests/test_augment_*.py` und laufen ohne Auggie:

```bash
python -m pytest tests/test_augment_healthcheck.py tests/test_augment_config.py \
  tests/test_augment_context_provider.py tests/test_auggie_cli_worker.py -q
```
