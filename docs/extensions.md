# Extensions & Custom Roles

Ananta erlaubt das Nachladen externer Module über `AGENT_EXTENSIONS`.

## Aktivierung

Setzen Sie die Umgebungsvariable:

```
AGENT_EXTENSIONS=custom.module,another.module
```

## Extension-Kontrakt

Ein Extension-Modul muss eine der folgenden Formen liefern:

- `init_app(app)` – registriert eigene Blueprints/Services
- `bp` oder `blueprint` – Flask-Blueprint

Beispiel:

```python
from flask import Blueprint

bp = Blueprint("custom", __name__)

@bp.route("/custom/ping")
def ping():
    return {"status": "ok"}
```

## Einsatz für neue Rollen

Neue Rollen können über eine Extension eigene Endpunkte, Workflows oder Validierungen hinzufügen.
Die Rollen-Logik bleibt in der Datenbank (`RoleDB`, `TeamTypeRoleLink`) konfigurierbar.
