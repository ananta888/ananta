# generate_code

```json
{
  "code_description": "Architekturvorschlag für eine FastAPI-Anwendung mit klarem Layering (API, Service, Repository/Data) in Python. Die Ausgabe soll beinhalten: 1. Projektstruktur und Module. 2. Detaillierte Beschreibung der Layer und deren Verantwortung. 3. Beispiel-Code-Strukturen (nur Skelette/Signaturen). 4. Liste der notwendigen Abhängigkeiten (requirements.txt). 5. Erläuterung der verwendeten Tools (Pydantic, Redis/etc.).",
  "context": "Der Vorschlag muss die Trennung der Verantwortlichkeiten (SoC) klar demonstrieren: API-Layer (HTTP-Anfragen, Pydantic-Validierung), Service-Layer (Geschäftslogik, Transaktionsmanagement), Repository/Data-Layer (Datenzugriff, Datenbank-Interaktion). Spezifische Abhängigkeiten wie Pydantic (Validierung) und die Nutzung eines Caches (z.B. Redis) oder ORMs (z.B. SQLAlchemy) müssen adressiert werden.",
  "output_format_constraints": "Der Output soll gut strukturiert sein und Code-Beispiele sowie textuelle Beschreibungen (z.B. Verantwortlichkeiten, Anforderungen) enthalten, aber keine funktionsfähige, ausführbare App, sondern einen detaillierten Entwurf."
}
```
