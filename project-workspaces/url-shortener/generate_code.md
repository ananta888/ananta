# generate_code

```json
{
  "context": "Schreibe Unit Tests für die Service-Schicht (z.B. Code-Generierung, DB-Speicherung, Zähler-Inkrementierung) unter Verwendung von `pytest`. Simuliere Datenbankinteraktionen, um die Geschäftslogik zu isolieren. Die Tests sollen die Geschäftslogik von der tatsächlichen Datenbankinteraktion trennen, indem Mocks verwendet werden. Wir gehen davon aus, dass es eine Service-Klasse gibt, z.B. `UserService`, und dass diese Abhängigkeiten (wie das Repository/die Datenbank-Schnittstelle) über Dependency Injection (DI) entgegennimmt.",
  "details": "Fokus auf Mocking von Datenbankaufrufen (z.B. mit `unittest.mock` oder `pytest-mock`). Speziell Beispiele für: 1. Erzeugen eines Benutzers. 2. Abrufen eines Benutzers per ID. 3. Aktualisieren von Benutzerdaten. 4. Funktion, die einen Zähler erhöht und prüfen, ob die Interaktion korrekt gemockt wird.",
  "file": "test_services.py",
  "language": "Python"
}
```
