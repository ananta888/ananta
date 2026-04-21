# Goal Input Schemas

Diese Seite dokumentiert strukturierte Eingaben fuer offizielle Goal-Modi. Die Felder sind bewusst produktnah benannt, damit UI, CLI und API denselben Kernpfad nutzen koennen.

## Neues Softwareprojekt anlegen

Modus: `new_software_project`

Pflichtfelder:

| Feld | Bedeutung |
| --- | --- |
| `project_idea` | Problem oder Produktidee, aus der ein Projekt entstehen soll. |
| `target_users` | Zielgruppe oder Nutzerrolle. |
| `platform` | Zielplattform, z.B. Web, CLI, Desktop, API oder Mobile. |

Optionale Felder:

| Feld | Bedeutung |
| --- | --- |
| `preferred_stack` | Gewuenschter oder vorhandener Tech-Stack. |
| `non_goals` | Was ausdruecklich nicht Teil des ersten Projekts ist. |
| `security_level` | Sicherheitsniveau fuer Planung und Ausfuehrung. |
| `execution_depth` | Planungstiefe: `quick`, `standard` oder `deep`. |

Unklare oder leere Pflichtfelder sollen im UI als Rueckfrage sichtbar bleiben und nicht still durch generische Defaults ersetzt werden.

## Existierendes Softwareprojekt weiterentwickeln

Modus: `project_evolution`

Pflichtfelder:

| Feld | Bedeutung |
| --- | --- |
| `change_goal` | Gewuenschte Aenderung am bestehenden Projekt. |
| `change_type` | Art der Weiterentwicklung: `kleine_erweiterung`, `refactoring`, `feature_ausbau` oder `technische_verbesserung`. |
| `risk_level` | Einschaetzung: `niedrig`, `mittel` oder `hoch`. |

Optionale Felder:

| Feld | Bedeutung |
| --- | --- |
| `affected_areas` | Dateien, Module, Pakete oder Oberflaechenbereiche. |
| `constraints` | Grenzen, Kompatibilitaetsregeln oder Governance-Vorgaben. |
| `execution_depth` | Planungstiefe: `quick`, `standard` oder `deep`. |

Der Hub bleibt fuer Planung, Routing, Queue und Governance verantwortlich. Worker duerfen aus diesen Eingaben nur delegierte Ausfuehrungsschritte bearbeiten.
