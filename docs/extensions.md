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

## Einsatz fuer neue Rollen

Neue Rollen koennen ueber eine Extension oder ein Plugin eigene Endpunkte, Workflows oder Validierungen hinzufuegen.
Die Rollen-Logik bleibt in der Datenbank (`RoleDB`, `TeamTypeRoleLink`) konfigurierbar.

