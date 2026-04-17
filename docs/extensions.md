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

## Evolution-Provider als Plugin

Evolution-Provider werden ueber das normale Plugin-System geladen und in die
hub-seitige Evolution-Registry eingetragen. Der Hub bleibt dabei die Control
Plane; Provider fuehren nur die delegierten SPI-Operationen aus.

Empfohlen ist die explizite Registrierung in `init_app(app)`:

```python
from agent.services.evolution import EvolutionEngine, register_evolution_provider


class MyEvolutionProvider(EvolutionEngine):
    ...


def init_app(app):
    register_evolution_provider(MyEvolutionProvider(), app=app, default=True)
```

Alternativ kann ein Plugin deklarativ `evolution_provider`,
`evolution_providers` oder `get_evolution_providers(app)` bereitstellen. Mit
`evolution_provider_default = True` wird der erste deklarierte Provider als
Default registriert.

Fehlerhafte Evolution-Plugins werden beim Laden geloggt und blockieren den
Rest des Systems nicht. Provider muessen mindestens `ANALYZE` unterstuetzen;
Validate und Apply bleiben Capability-gesteuert und policy-abhaengig.

## Einsatz fuer neue Rollen

Neue Rollen koennen ueber eine Extension oder ein Plugin eigene Endpunkte, Workflows oder Validierungen hinzufuegen.
Die Rollen-Logik bleibt in der Datenbank (`RoleDB`, `TeamTypeRoleLink`) konfigurierbar.
