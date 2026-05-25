# Worker Result Artifact Policy

## Ziel

Neu erzeugte Worker-Artefakte werden automatisch klassifiziert und nicht unkontrolliert weitergegeben.

## Default-Klassifikation

- Jedes neue Worker-Resultat startet mit einer expliziten Default-Klassifikation.
- Ohne Klassifikation gilt keine Weitergabe-Freigabe.

## Vererbungsregel

- Wenn Input-Artefakte `restricted` oder `secret` sind, erbt das Resultat mindestens dieselbe Schutzklasse.
- Downgrade der Klasse erfordert explizite Policy-Entscheidung und Audit.

## Freigaberegeln

- Weitergabe von Worker-Resultaten an User/Worker braucht erneute Grant-Pruefung.
- `download_encrypted`, `decrypt`, `share`, `provide_to_worker`, `provide_to_remote_llm` bleiben getrennte Rechte.
- Resultatfreigaben sind zeit- und scope-begrenzt.

## Trust-Grenze

- Worker darf Klassifikation nicht allein final setzen.
- Finalisierung erfolgt durch Hub-Policy (optional mit Human Approval fuer sensitive Klassen).
