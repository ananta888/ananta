# Source Line Limit Policy

Produktiv-Source-Dateien sollen höchstens 1000 Zeilen enthalten.

Regeln:

- `category=source`: harte Zielgrenze `<=1000` Zeilen.
- `category=test`: Testdateien dürfen temporär größer sein, müssen aber im
  Audit separat sichtbar bleiben.
- `category=generated`: generierte oder minifizierte Dateien zählen nicht als
  Refactor-Ziel.
- `category=excluded`: Build-, Dependency-, Workspace- und Runtime-Ausgaben
  werden nicht rekursiv geprüft.

Eine Produktivdatei über 1000 Zeilen darf nur temporär in
`reports/source-files-over-1000-allowlist.json` stehen. Jeder Eintrag braucht
eine Begründung und ein Ablaufdatum oder einen Follow-up-Task. Neue
nicht-allowlistete Produktivdateien über 1000 Zeilen müssen im Gate fehlschlagen.

Facade-Dateien knapp über 1000 Zeilen sind nur als Übergang akzeptabel, wenn
die eigentliche Logik bereits in fokussierte Module wandert und ein Follow-up
die Fassade weiter reduziert.
