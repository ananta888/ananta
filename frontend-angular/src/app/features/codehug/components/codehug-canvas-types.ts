// ─── Static Config Data (mirrored from Hub DB) ───────────────────────────────

export interface BlueprintDef { id: string; name: string; roles: string[]; }
export interface PlaybookTask { id: string; title: string; description: string; priority: 'High' | 'Medium' | 'Low'; }
export interface PlaybookDef { id: string; name: string; tasks: PlaybookTask[]; }

export const BLUEPRINTS: BlueprintDef[] = [
  { id: 'scrum', name: 'Scrum', roles: ['Product Owner', 'Scrum Master', 'Developer'] },
  { id: 'scrum-opencode', name: 'Scrum-OpenCode', roles: ['Product Owner', 'Scrum Master', 'Developer'] },
  { id: 'kanban', name: 'Kanban', roles: ['Service Delivery Manager', 'Flow Manager', 'Developer'] },
  { id: 'tdd', name: 'TDD', roles: ['Behavior Analyst', 'Test Driver', 'Refactor Verifier'] },
  { id: 'code-repair', name: 'Code-Repair', roles: ['Repair Lead', 'Fix Engineer', 'QA Verifier'] },
  { id: 'research', name: 'Research', roles: ['Research Lead', 'Source Analyst', 'Reviewer'] },
  { id: 'security-review', name: 'Security-Review', roles: ['Security Lead', 'Security Analyst', 'Compliance Reviewer'] },
  { id: 'release-prep', name: 'Release-Prep', roles: ['Release Manager', 'Verification Engineer', 'Operations Liaison'] },
  { id: 'story-domain', name: 'Story-Domain', roles: ['Story Analyst', 'Domain Modeler', 'Implementation Coder', 'Verification Tester'] },
  { id: 'research-evolution', name: 'Research-Evolution', roles: ['Research Lead', 'Evolution Strategist', 'Review Gate Owner'] },
];

export const PLAYBOOKS: PlaybookDef[] = [
  { id: 'bug_fix', name: 'Bug Fix', tasks: [
    { id: 't1', title: 'Bug reproduzieren', description: 'Reproduktionsschritte dokumentieren', priority: 'High' },
    { id: 't2', title: 'Root Cause Analyse', description: 'Ursache identifizieren', priority: 'High' },
    { id: 't3', title: 'Fix implementieren', description: 'Korrektur umsetzen', priority: 'High' },
    { id: 't4', title: 'Test schreiben', description: 'Unit/Integration-Test erstellen', priority: 'Medium' },
    { id: 't5', title: 'Code Review', description: 'Fix zur Prüfung einreichen', priority: 'Medium' },
  ]},
  { id: 'feature', name: 'Feature', tasks: [
    { id: 't1', title: 'Anforderungen definieren', description: 'Funktionale & nicht-funktionale Anforderungen', priority: 'High' },
    { id: 't2', title: 'Design / Architektur', description: 'Technisches Design erstellen', priority: 'High' },
    { id: 't3', title: 'Implementierung', description: 'Feature implementieren', priority: 'High' },
    { id: 't4', title: 'Tests schreiben', description: 'Unit und Integration Tests', priority: 'Medium' },
    { id: 't5', title: 'Dokumentation', description: 'Feature dokumentieren', priority: 'Low' },
  ]},
  { id: 'tdd', name: 'TDD', tasks: [
    { id: 't1', title: 'Verhalten klären', description: 'Akzeptanzkriterien festhalten', priority: 'High' },
    { id: 't2', title: 'Test zuerst', description: 'Test für Zielverhalten erstellen', priority: 'High' },
    { id: 't3', title: 'Red-Phase', description: 'Test läuft fehl – Evidenz sichern', priority: 'High' },
    { id: 't4', title: 'Minimaler Patch', description: 'Kleinste Änderung umsetzen', priority: 'High' },
    { id: 't5', title: 'Green-Phase', description: 'Tests bestehen verifizieren', priority: 'High' },
    { id: 't6', title: 'Refactoring', description: 'Qualität verbessern', priority: 'Medium' },
    { id: 't7', title: 'Finale Verifikation', description: 'Abschluss + Approval-Gate', priority: 'Medium' },
  ]},
  { id: 'refactor', name: 'Refactoring', tasks: [
    { id: 't1', title: 'Code-Analyse', description: 'Verbesserungspotenzial identifizieren', priority: 'Medium' },
    { id: 't2', title: 'Refactoring-Plan', description: 'Schritte planen', priority: 'Medium' },
    { id: 't3', title: 'Refactoring', description: 'Code umstrukturieren', priority: 'Medium' },
    { id: 't4', title: 'Tests verifizieren', description: 'Alle Tests noch grün', priority: 'High' },
  ]},
  { id: 'test', name: 'Testing', tasks: [
    { id: 't1', title: 'Test-Strategie', description: 'Strategie und Abdeckung definieren', priority: 'High' },
    { id: 't2', title: 'Unit Tests', description: 'Unit Tests schreiben', priority: 'High' },
    { id: 't3', title: 'Integration Tests', description: 'Integration Tests implementieren', priority: 'Medium' },
    { id: 't4', title: 'Coverage-Report', description: 'Abdeckung analysieren', priority: 'Low' },
  ]},
  { id: 'architecture_review', name: 'Architektur-Review', tasks: [
    { id: 't1', title: 'Struktur-Audit', description: 'Modulabhängigkeiten und Boundaries', priority: 'Medium' },
    { id: 't2', title: 'SOLID Check', description: 'Engineering-Prinzipien untersuchen', priority: 'Medium' },
    { id: 't3', title: 'Design-Docs', description: 'ADRs sichten oder erstellen', priority: 'Low' },
    { id: 't4', title: 'Empfehlungsliste', description: 'Design-Verbesserungen vorschlagen', priority: 'Medium' },
  ]},
  { id: 'incident', name: 'Incident', tasks: [
    { id: 't1', title: 'Systemstatus prüfen', description: 'Logs und Metriken sofort scannen', priority: 'High' },
    { id: 't2', title: 'Eingrenzung', description: 'Betroffene Komponente identifizieren', priority: 'High' },
    { id: 't3', title: 'Mitigation', description: 'Sofortmaßnahmen einleiten', priority: 'High' },
    { id: 't4', title: 'Post-Mortem', description: 'Ursache dokumentieren', priority: 'Medium' },
  ]},
  { id: 'repo_analysis', name: 'Repo-Analyse', tasks: [
    { id: 't1', title: 'Projektstruktur scannen', description: 'Ordnerstruktur auflisten', priority: 'High' },
    { id: 't2', title: 'Abhängigkeiten prüfen', description: 'Bibliotheken auf Aktualität', priority: 'Medium' },
    { id: 't3', title: 'Code-Qualität', description: 'Stichproben SOLID-Prinzipien', priority: 'Medium' },
    { id: 't4', title: 'Sicherheits-Audit', description: 'Offensichtliche Lücken suchen', priority: 'High' },
    { id: 't5', title: 'Analyse-Bericht', description: 'Strukturiertes Artefakt', priority: 'Medium' },
  ]},
];

// ─── Canvas Types ─────────────────────────────────────────────────────────────

export type NodeType = 'start' | 'planning' | 'task' | 'det' | 'gate' | 'review' | 'verification' | 'end' | 'fork' | 'join';
export type EdgeCondition = 'always' | 'on_success' | 'on_failure' | 'back_edge' | 'on_output';
export type Priority = 'High' | 'Medium' | 'Low';
export type RoutingMode = 'auto' | 'backend' | 'worker' | 'capability';
export type DetSubtype = 'script' | 'api-call' | 'regex-check' | 'git-op' | 'file-check';
export type GateSubtype = 'auto-verify' | 'human-approval' | 'test-run' | 'lint' | 'type-check';
export type FailAction = 'block' | 'continue' | 'rollback' | 'retry';

export const VP_KINDS = ['coding', 'analysis', 'run_tests', 'code_review', 'refactor', 'bugfix',
  'research', 'llm_generate', 'goal_plan', 'deploy', 'spec', 'breakdown'] as const;

export interface StepRouting {
  mode: RoutingMode;
  backend?: string;
  workerName?: string;
  capability?: string;
}

export interface ArtifactSlot {
  name: string;
  kind: 'code' | 'text' | 'json' | 'report' | 'binary' | 'file';
  required: boolean;
  description: string;
  producedByStepId?: string;
  producedByOutputName?: string;
}

export interface CanvasNode {
  id: string;
  x: number; y: number;
  w: number; h: number;
  type: NodeType;
  title: string;
  subtitle?: string;
  role?: string;
  inputs: ArtifactSlot[];
  outputs: ArtifactSlot[];
  skillProfileId?: string;
  vpKind?: string;
  gate?: boolean;
  routing?: StepRouting;
  detSubtype?: DetSubtype;
  detCommand?: string;
  detExpectedResult?: string;
  failAction?: FailAction;
  gateSubtype?: GateSubtype;
  gateTimeout?: number;
  priority?: Priority;
  enabled: boolean;
}

export const BACKENDS = ['ananta', 'opencode', 'hermes', 'sgpt', 'claude', 'lmstudio', 'ollama'] as const;
export const CAPABILITIES = ['planner', 'researcher', 'coder', 'reviewer', 'tester'] as const;

export const NODE_STYLE: Record<string, { fill: string; stroke: string; dash?: string }> = {
  task:         { fill: 'white',    stroke: '#d1d5db' },
  det:          { fill: '#fefce8',  stroke: '#ca8a04', dash: '5,3' },
  gate:         { fill: '#fff7ed',  stroke: '#ea580c', dash: '5,3' },
  review:       { fill: '#faf5ff',  stroke: '#9333ea' },
  planning:     { fill: '#e0e7ff',  stroke: '#4f46e5' },
  verification: { fill: '#d1fae5',  stroke: '#059669' },
  start:        { fill: '#fef3c7',  stroke: '#d97706' },
  end:          { fill: '#f0fdf4',  stroke: '#16a34a' },
  fork:         { fill: '#fdf4ff',  stroke: '#a855f7' },
  join:         { fill: '#f0f9ff',  stroke: '#0284c7' },
};

export interface CanvasEdge {
  id: string;
  from: string;
  to: string;
  condition: EdgeCondition;
  label?: string;
  loopMaxIter?: number;
  outputName?: string;
  bindings?: ArtifactBinding[];
}

export interface ArtifactBinding {
  outputName: string;
  inputName: string;
}

export const NODE_W = 220;
export const NODE_H = 68;
export const GAP_Y = 52;
export const CX = 300;

export const PRIORITY_COLOR: Record<Priority, string> = { High: '#ef4444', Medium: '#f59e0b', Low: '#22c55e' };
export const COND_COLOR: Record<EdgeCondition, string> = { always: '#9ca3af', on_success: '#22c55e', on_failure: '#ef4444', back_edge: '#7c3aed', on_output: '#0284c7' };
export const ARTIFACT_KINDS = ['code', 'text', 'json', 'report', 'binary', 'file'] as const;
