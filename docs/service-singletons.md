# Service-Singletons — kanonisches Pattern (CLN-035)

## Status quo

`agent/services/` trägt ~300 handgeschriebene `get_*_service()`-Accessor-
Funktionen aus vier konkurrierenden Mustern:

1. eager Modul-Level-Instanz (`x = Service()`)
2. lazy globales `_service` + `get_x_service()`
3. `current_app.extensions` (Flask-Container als Service-Locator)
4. Late-Binding über den DI-Seam (`agent.services.di`)

Ein Big-Bang-Rewrite lohnt sich nicht. Stattdessen:

- **Wachstum eingefroren:** `scripts/check_singleton_freeze.py` ratcht die
  Menge der Accessor-Module (`reports/singleton-freeze.txt`). NEUE
  `get_*_service()`-Funktionen failen den Check.
- **Kanonisches Pattern für NEUE Services** (siehe unten).
- Bestandsaccessoren wandern inkrementell, wenn ihr Modus eh angefasst wird.

## Kanonisches Pattern für neue Services

1. **Konstruktor-Injection am Composition Root** (`agent/bootstrap/*`):
   Abhängigkeiten kommen per Konstruktor, nicht per Import-Singleton.

   ```python
   # agent/services/mail_digest_service.py
   class MailDigestService:
       def __init__(self, mail_accounts: MailAccountPort, audit: AuditPort) -> None:
           self._accounts = mail_accounts
           self._audit = audit
   ```

   ```python
   # agent/bootstrap/mail_services.py (Composition Root)
   def register_mail_services(app):
       app.extensions["mail_digest_service"] = MailDigestService(
           mail_accounts=get_mail_account_port(),
           audit=get_audit_port(),
       )
   ```

2. **Repository-Late-Binding über den DI-Seam** (`agent.services.di`,
   siehe `docs/di-layer.md`): `get_repository_registry()` ist der kanonische
   Injektionspunkt für Test-Substitution.

3. **Kein neuer `get_*_service()`-Accessor** — der Freeze-Detektor
   (`scripts/check_singleton_freeze.py`, in der Check-Pipeline) lehnt ihn ab.

## SOLID

- **DIP:** Services hängen an injizierten Ports, nicht an konkreten
  Import-Singletons.
- **SRP:** Wiring liegt im Composition Root, nicht im Service.
- **OCP:** Neue Services entstehen durch neuen Bootstrap-Wiring, ohne
  bestehende Accessoren anzufassen.
