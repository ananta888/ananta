import { DemoPreviewExample } from './dashboard-demo-preview.component';

/**
 * Built-in goal presets shown on the dashboard when the hub provides no demo
 * preview examples. Pure data, kept apart from the dashboard component.
 */
export const DEFAULT_GOAL_PRESETS: DemoPreviewExample[] = [
  {
    id: 'repo-analysis',
    title: 'Repository verstehen',
    goal: 'Analysiere dieses Repository und schlage die wichtigsten naechsten Schritte vor.',
    outcome: 'Hotspots, Risiken und ein kurzer Arbeitsplan.',
    tasks: ['Projektstruktur lesen', 'Architekturgrenzen pruefen', 'Review-Plan erstellen'],
    starter_context: 'Fokus: Einstieg fuer neue Maintainer, Risiken benennen, keine Code-Aenderungen.',
  },
  {
    id: 'bugfix-plan',
    title: 'Bugfix planen',
    goal: 'Untersuche einen Fehlerbericht und plane eine kleine, testbare Korrektur.',
    outcome: 'Reproduktionspfad, Ursache und Regressionstest.',
    tasks: ['Fehler reproduzieren', 'Betroffene Pfade finden', 'Fix und Regressionstest vorschlagen'],
    starter_context: 'Fokus: kleine, testbare Korrektur planen und Regressionen vermeiden.',
  },
  {
    id: 'compose-diagnosis',
    title: 'Start reparieren',
    goal: 'Pruefe Docker- und Compose-Probleme und leite eine robuste lokale Startsequenz ab.',
    outcome: 'Konkrete Startbefehle und naechste Diagnose.',
    tasks: ['Compose-Profile pruefen', 'Ports und Health-Checks auswerten', 'Startpfad dokumentieren'],
    starter_context: 'Fokus: lokaler Start, Compose-Profile, Health-Checks und klare naechste Diagnose.',
  },
  {
    id: 'change-review',
    title: 'Change Review',
    goal: 'Fuehre ein Review durch: priorisiere Risiken, benoetigte Tests und mögliche Regressionen.',
    outcome: 'Findings nach Schweregrad + konkrete naechste Checks.',
    tasks: ['Diff/Hotspots pruefen', 'Risiken und Regressionen priorisieren', 'Testplan fuer Verifikation'],
    starter_context: 'Fokus: Review statt Implementierung. Keine automatischen Aenderungen ohne explizite Freigabe.',
  },
  {
    id: 'guided-first-run',
    title: 'Gefuehrter erster Lauf',
    goal: 'Erstelle ein erstes kontrolliertes Goal mit Kontext, Ausfuehrungstiefe und Sicherheitsniveau.',
    outcome: 'Parametrisiertes Goal mit sichtbaren Safety- und Review-Entscheidungen.',
    tasks: ['Ziel klaeren', 'Kontext sammeln', 'Sicherheitsniveau pruefen'],
    starter_context: 'Fokus: Erstnutzerfuehrung, sichtbare Governance und klarer naechster Schritt.',
  },
  {
    id: 'new-software-project',
    title: 'Neues Projekt anlegen',
    goal: 'Lege ein neues Softwareprojekt aus einer Idee an und erstelle Scope, Architekturvorschlag, initiales Backlog und sichere naechste Schritte.',
    outcome: 'Reviewbarer Projekt-Blueprint mit kleinen Initial-Tasks.',
    tasks: ['Projektidee klaeren', 'Blueprint erstellen', 'Initial-Tasks priorisieren'],
    starter_context: 'Fokus: neuer Projektstart, sichere Defaults, keine Vollautomatik ohne Review.',
  },
  {
    id: 'research-evolution',
    title: 'Research -> Proposal -> Review',
    goal: 'Erweitere ein bestehendes Projekt um ein kleines Feature; recherchiere zuerst relevante Quellen und erstelle danach reviewbare Evolver-Proposals.',
    outcome: 'Research-Bericht, reviewbares Proposal und sichtbares Review-Gate.',
    tasks: ['Scope schaerfen', 'Research-Artefakt erstellen', 'Proposal und Review-Gate vorbereiten'],
    starter_context: 'Fokus: DeerFlow fuer Recherche, danach Evolver fuer kontrollierte Proposals. Keine impliziten Apply-Schritte.',
    path_summary: 'Der Standardpfad fuehrt von Goal ueber Research zu Proposal und Review, nicht direkt zu verdeckter Ausfuehrung.',
    artifacts: ['Research Summary', 'Source List', 'Evolver Proposal', 'Review Gate'],
    governance: ['Hub haelt Review und Policy sichtbar.', 'Apply bleibt standardmaessig deaktiviert.'],
  },
  {
    id: 'project-evolution',
    title: 'Projekt weiterentwickeln',
    goal: 'Plane eine kontrollierte Weiterentwicklung eines bestehenden Projekts mit betroffenen Bereichen, Risiken, Tests und Review-Schritten.',
    outcome: 'Kleiner, verifizierbarer Aenderungsplan fuer ein bestehendes Repository.',
    tasks: ['Ist-Kontext schaerfen', 'Aenderungsschritte zerlegen', 'Tests und Risiken pruefen'],
    starter_context: 'Fokus: aktive Weiterentwicklung statt Repository verstehen, kleine pruefbare Aenderungen mit Review.',
  },
];
