# Extensions & Custom Roles

Ananta erlaubt das Nachladen externer Module ueber `AGENT_EXTENSIONS`.

## Aktivierung

Setzen Sie die Umgebungsvariable:

```
AGENT_EXTENSIONS=custom.module,another.module
```

Optional koennen Plugins automatisch aus Verzeichnissen geladen werden:

```
AGENT_PLUGIN_DIRS=plugins,custom_plugins
AGENT_PLUGINS=my_explicit_plugin
```

## Extension-Kontrakt

Ein Extension-Modul muss eine der folgenden Formen liefern:

- `init_app(app)` - registriert eigene Blueprints/Services
- `bp` oder `blueprint` - Flask-Blueprint

Beispiel:

```python
from flask import Blueprint

bp = Blueprint("custom", __name__)

@bp.route("/custom/ping")
def ping():
    return {"status": "ok"}
```

## Plugin Discovery

- Jedes Untermodul mit `__init__.py` in `AGENT_PLUGIN_DIRS` wird als Plugin versucht.
- Alternativ koennen Plugin-Modulnamen direkt in `AGENT_PLUGINS` angegeben werden.
- Plugin-Kontrakt identisch zu Extensions: `init_app(app)` oder `bp`/`blueprint`.
- Optional koennen Plugins ein Manifest `ananta-plugin.json` im Plugin-Paket oder `<plugin>.plugin.json` neben einem Modul bereitstellen.
- Das Manifest kann `name`, `type`, `provider`, `enabled`, `replace`, `default_roles` und `compatibility` deklarieren.
- `enabled: false` deaktiviert ein Plugin kontrolliert; der Startup-Report weist es als `disabled` aus, ohne das Modul zu importieren.
- Der strukturierte Report steht unter `app.extensions["plugin_startup_report"]`.

## Strenges Extension-Modell (Core-Guardrails)

- Erweiterungen sind capability-gebunden und duerfen den Hub-Governance-Kern nicht umgehen.
- Extension-Seams bleiben bewusst klein:
  - Blueprint/Route-Registrierung ueber `init_app(app)` oder Blueprint-Objekt
  - Evolution-Erweiterung ueber `EvolutionEngine` und SDK-Registrierung
- Erweiterungen duerfen keine direkte Worker-zu-Worker-Orchestrierung einfuehren.
- Fehler in einzelnen Plugins duerfen den Hub-Start nicht global blockieren.

## Contracts fuer Action Packs und Skills

- Erweiterungen sollen neue Faehigkeiten als klaren Contract exponieren (statt direkter Core-Mutationen).
- Capability-Grenzen muessen mechanisch pruefbar bleiben (z. B. ueber Tests wie `test_plugin_contract_boundaries.py`).
- Action-Pack- oder Skill-nahe Erweiterungen muessen mit bestehenden Policy-/Audit-Pfaden kompatibel bleiben.

## Externe Adapter: pilotiert und begrenzt

- Externe Adapter werden erst nach stabilem Kernzugang (Web UI, CLI, API/Webhook) erweitert.
- Der erste externe Adapter bleibt ein bewusst kleiner Pilot mit harter Policy-Grenze.
- In diesem Projekt ist der optionale Evolver-Provider der referenzierte Pilotpfad fuer externe Capability-Integration.

## Oekosystem/Marktplatz nur nach reifen Kern-Contracts

- Marktplatz- oder breitere Oekosystem-Ideen sind explizit nachgelagert.
- Voraussetzung sind stabile, gehaertete Kern-Contracts und bewiesene Governance-/Audit-Reife.

## Evolution-Provider als Plugin

Evolution-Provider werden ueber das normale Plugin-System geladen und in die
hub-seitige Evolution-Registry eingetragen. Der Hub bleibt dabei die Control
Plane; Provider fuehren nur die delegierten SPI-Operationen aus.

Empfohlen ist die Nutzung des **Ananta SDKs** in `init_app(app)`:

```python
from agent.sdk import get_sdk, EvolutionEngine

class MyEvolutionProvider(EvolutionEngine):
    provider_name = "my_provider"
    # ... Implementierung ...

def init_app(app):
    sdk = get_sdk(app)
    sdk.register_evolution_provider(MyEvolutionProvider(), default=True)
```

Das SDK bietet eine stabile Schnittstelle und schirmt Plugins vor internen Refactorings ab.
Folgende Methoden stehen im `AnantaSDK` zur Verfügung:

- `register_evolution_provider(engine, default=False, replace=False)`
- `register_blueprint(blueprint)`
- `get_config(section=None)` -> gibt die Agenten-Konfiguration zurück

Alternativ kann ein Plugin deklarativ `evolution_provider`,
`evolution_providers` oder `get_evolution_providers(app)` bereitstellen. Mit
`evolution_provider_default = True` wird der erste deklarierte Provider als
Default registriert.

Ein Plugin darf nicht denselben Provider gleichzeitig deklarativ und imperativ
registrieren. Wenn `init_app(app)` den Provider bereits registriert hat, wird
eine identische deklarative Angabe nur als kompatibler Altfall uebersprungen.
Konflikte mit bereits registrierten Providernamen schlagen ohne explizites
`evolution_provider_replace = True` fehl.

Fehlerhafte Evolution-Plugins werden beim Laden geloggt und blockieren den
Rest des Systems nicht. Provider muessen mindestens `ANALYZE` unterstuetzen;
Validate und Apply bleiben Capability-gesteuert und policy-abhaengig.

Der erste konkrete Provider ist der optionale Evolver-Adapter. Betriebsdetails
stehen in `docs/evolver-adapter.md`.

## Einsatz fuer neue Rollen

Neue Rollen koennen ueber eine Extension oder ein Plugin eigene Endpunkte, Workflows oder Validierungen hinzufuegen.
Die Rollen-Logik bleibt in der Datenbank (`RoleDB`, `TeamTypeRoleLink`) konfigurierbar.
