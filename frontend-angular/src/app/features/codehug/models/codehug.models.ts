/**
 * CodeHug Models — typed read/write models for the CodeHug special view.
 *
 * Diese Models kapseln alle Daten die zwischen CodeHug und dem Hub ausgetauscht
 * werden. Sie sind unabhaengig von anderen Features (kein Import aus
 * features/codecompass-graph, features/context-access-policy etc.).
 *
 * Naming-Konvention: CH* fuer CodeHug-spezifische Typen. Read models enden
 * auf ReadModel, write requests auf Request, responses auf Response.
 */

// ─────────────────────────────────────────────────────────────────────────────
// CodeCompass Integration
// ─────────────────────────────────────────────────────────────────────────────

/** Index-Status eines CodeCompass-Projekts. */
export type ChIndexStatus = 'complete' | 'partial' | 'missing' | 'running' | 'error';

/** Projekt-Metadaten aus CodeCompass. */
export interface ChProjectReadModel {
  id: string;
  name: string;
  rootPath: string;
  languageBreakdown: Record<string, number>;
  frameworkSignals: string[];
  moduleCount: number;
  fileCount: number;
  symbolCount: number;
  lastIndexedAt: number | null;
  indexStatus: ChIndexStatus;
}

/** Symboltyp (Klasse, Funktion, Interface, ...). */
export type ChSymbolKind =
  | 'class'
  | 'function'
  | 'method'
  | 'interface'
  | 'enum'
  | 'struct'
  | 'trait'
  | 'module'
  | 'constant'
  | 'variable';

/** Ein erkanntes Symbol in der Codebase. */
export interface ChSymbolReadModel {
  id: string;
  name: string;
  qualifiedName: string;
  kind: ChSymbolKind;
  filePath: string;
  lineStart: number;
  lineEnd: number;
  signature?: string;
  visibility: 'public' | 'internal' | 'private' | 'unknown';
  docSummary?: string;
}

/** Eine Datei in der Codebase. */
export interface ChFileReadModel {
  path: string;
  language: string;
  sizeBytes: number;
  lastModified: number;
  symbolIds: string[];
  isSensitive: boolean;
}

/** Kontextvorschlag fuer ein Kontextpaket. */
export interface ChContextSuggestionReadModel {
  symbolId?: string;
  filePath?: string;
  reason: string;
  relevanceScore: number; // 0..1
  source: 'resolve_context' | 'plan_context' | 'search_symbols' | 'manual';
}

/** Request fuer resolve_context. */
export interface ChResolveContextRequest {
  projectId: string;
  taskDescription: string;
  maxSuggestions?: number;
}

/** Response fuer resolve_context. */
export interface ChResolveContextResponse {
  suggestions: ChContextSuggestionReadModel[];
  resolvedSymbols: ChSymbolReadModel[];
  estimatedTokenCount: number;
}

/** Request fuer search_symbols. */
export interface ChSearchSymbolsRequest {
  projectId: string;
  query: string;
  kinds?: ChSymbolKind[];
  limit?: number;
}

/** Response fuer search_symbols. */
export interface ChSearchSymbolsResponse {
  symbols: ChSymbolReadModel[];
  totalMatches: number;
}

/** Request fuer get_file_context. */
export interface ChGetFileContextRequest {
  projectId: string;
  filePath: string;
  includeSymbols?: boolean;
}

/** Response fuer get_file_context. */
export interface ChGetFileContextResponse {
  file: ChFileReadModel;
  symbols: ChSymbolReadModel[];
  /** Deterministisch aus dem Code abgeleitete Fakten. */
  deterministicFacts: ChDeterministicFact[];
  /** KI-generierte Zusammenfassung, klar getrennt von Fakten. */
  llmSummary: string | null;
  llmSummaryConfidence: number | null;
}

export interface ChDeterministicFact {
  key: string;
  value: string;
  source: 'parser' | 'index' | 'config' | 'policy';
}

/** Request fuer plan_context (Plan-basiertes Kontext-Packing). */
export interface ChPlanContextRequest {
  projectId: string;
  taskDescription: string;
  strategy?: 'breadth' | 'depth' | 'anchored';
}

export interface ChPlanContextResponse {
  groups: ChContextGroup[];
  warnings: string[];
  estimatedTokenCount: number;
}

export interface ChContextGroup {
  name: string;
  description: string;
  filePaths: string[];
  symbolIds: string[];
  reasoning: string;
  estimatedTokens: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent Run Integration
// ─────────────────────────────────────────────────────────────────────────────

/** Phase eines Agent-Run-Schritts. */
export type ChRunPhase = 'plan' | 'det' | 'llm' | 'apply' | 'verify' | 'tool' | 'policy';

/** Status eines Agent-Run. */
export type ChRunStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'awaiting_diff_review'
  | 'awaiting_apply_confirmation'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'rolled_back';

/** Worker-Backend-Typ, der tatsaechlich fuer einen Schritt genutzt wurde. */
export type ChCliBackend = 'sgpt' | 'opencode' | 'codex' | 'claude_code' | 'aider' | 'mistral' | 'deterministic' | 'unknown';

/** Provider des LLMs, falls einer genutzt wurde. */
export type ChLlmProvider = 'ollama' | 'lmstudio' | 'openai' | 'anthropic' | 'openrouter' | 'custom' | 'none';

/** Ein Agenten-Profil (Template fuer Aufgaben). */
export interface ChAgentProfileReadModel {
  id: string;
  name: string;
  purpose: string;
  /** Rechte, die das Profil bekommt. */
  capabilities: ChProfileCapabilities;
  /** Modell das fuer dieses Profil verwendet wird. */
  model: string;
  /** Welcher cli_backend tatsaechlich fuer dieses Profil konfiguriert ist. */
  cliBackend: ChCliBackend;
  /** Provider des Modells. */
  llmProvider: ChLlmProvider;
  /** Liste erlaubter Tools. */
  allowedTools: string[];
  /** Liste erlaubter Pfade (leer = alles). */
  allowedPaths: string[];
  /** Maximale Token / Kontext-Groesse. */
  maxContextTokens: number;
  /** Maximale Laufzeit. */
  maxRuntimeMs: number;
}

export interface ChProfileCapabilities {
  canRead: boolean;
  canWrite: boolean;
  canExecute: boolean;
  canNetwork: boolean;
  canUseTools: boolean;
  requiresHumanApproval: boolean;
}

/** Request zum Starten eines Agent-Run. */
export interface ChStartAgentRunRequest {
  projectId: string;
  profileId: string;
  taskDescription: string;
  contextPackageId?: string;
  riskLevel: 'low' | 'medium' | 'high';
  /** Wenn false: Agent darf nichts schreiben, nur analysieren. */
  writeArmed: boolean;
  templateId?: string;
}

/** Status-Response eines Agent-Run. */
export interface ChAgentRunReadModel {
  id: string;
  status: ChRunStatus;
  projectId: string;
  profileId: string;
  startedAt: number;
  finishedAt: number | null;
  durationMs: number | null;
  writeArmed: boolean;
  steps: ChAgentStepReadModel[];
  /** Tatsaechlich genutzter cli_backend (kann vom konfigurierten abweichen). */
  actualCliBackend: ChCliBackend;
  /** Tatsaechlich genutztes Modell. */
  actualModel: string;
  /** Tatsaechlich genutzter Provider. */
  actualProvider: ChLlmProvider;
  /** Anzahl deterministischer vs LLM-Schritte. */
  deterministicStepCount: number;
  llmStepCount: number;
  /** Erklaerung, warum dieser Backend gewaehlt wurde. */
  routingReason: string;
  /** Eingelagerte Policy-Snapshot-ID. */
  policySnapshotId: string | null;
  /** Blockierende Fehler oder Warnungen. */
  warnings: string[];
}

/** Ein einzelner Schritt innerhalb eines Runs. */
export interface ChAgentStepReadModel {
  id: string;
  index: number;
  phase: ChRunPhase;
  title: string;
  startedAt: number;
  finishedAt: number | null;
  durationMs: number | null;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
  /** Welcher Worker den Schritt ausgefuehrt hat. */
  workerId?: string;
  /** Welches cli_backend. */
  cliBackend?: ChCliBackend;
  /** Welches Modell (oder 'deterministic'). */
  model?: string;
  /** Tool-Calls in diesem Schritt (nur in 'details'/'raw' sichtbar). */
  toolCalls?: ChToolCallReadModel[];
  /** Kurze Output-Beschreibung (immer sichtbar). */
  outputSummary?: string;
  /** Roher Output (nur 'raw' sichtbar). */
  rawOutput?: string;
  stderr?: string;
  args?: Record<string, unknown>;
  errorMessage?: string;
}

export interface ChToolCallReadModel {
  id: string;
  toolName: string;
  riskLevel: 'low' | 'medium' | 'high';
  targetPath?: string | null;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'denied';
  inputSummary: string;
  outputSummary?: string;
  startedAt: number;
  finishedAt: number | null;
}

/** Diff-Vorschau fuer eine geplante Aenderung. */
export interface ChDiffPreviewReadModel {
  filePath: string;
  additions: number;
  deletions: number;
  /** Unified diff formatiert. */
  unifiedDiff: string;
  /** Status pro Diff-Item. */
  decision: 'pending' | 'accepted' | 'rejected';
}

export interface ChDiffPreviewResponse {
  runId: string;
  diffs: ChDiffPreviewReadModel[];
  /** Verwendete Kontextpaket-ID, falls vorhanden. */
  contextPackageId?: string;
}

/** Anfrage zum Anwenden eines freigegebenen Diffs. */
export interface ChApplyDiffRequest {
  runId: string;
  acceptedFilePaths: string[];
  /** Muss vom Nutzer explizit bestaetigt werden. */
  applyConfirmationToken: string;
}

export interface ChApplyDiffResponse {
  applied: string[];
  failed: { filePath: string; reason: string }[];
  verificationTriggered: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Context Package Persistence
// ─────────────────────────────────────────────────────────────────────────────

export interface ChContextPackageReadModel {
  id: string;
  projectId: string;
  name: string;
  description?: string;
  version: number;
  createdAt: number;
  updatedAt: number;
  /** Liste der enthaltenen Dateien. */
  filePaths: string[];
  /** Liste der enthaltenen Symbole. */
  symbolIds: string[];
  /** Liste der enthaltenen Kontext-Gruppen. */
  contextGroups?: ChContextGroup[];
  /** Begruendungen pro Eintrag. */
  reasons: Record<string, string>;
  /** Geschaetzte Token-Groesse. */
  estimatedTokenCount: number;
  /** Zugeordnete Aufgabe. */
  taskDescription?: string;
  /** Welche Konfiguration gehoert dazu. */
  policySnapshotId?: string;
}

export interface ChContextPackageCreateRequest {
  projectId: string;
  name: string;
  description?: string;
  filePaths: string[];
  symbolIds: string[];
  reasons: Record<string, string>;
  taskDescription?: string;
}

export interface ChContextPackageUpdateRequest {
  name?: string;
  description?: string;
  filePaths?: string[];
  symbolIds?: string[];
  reasons?: Record<string, string>;
  taskDescription?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Templates (Aufgaben-Vorlagen)
// ─────────────────────────────────────────────────────────────────────────────

export interface ChTaskTemplateReadModel {
  id: string;
  name: string;
  taskDescription: string;
  defaultProfileId?: string;
  defaultRiskLevel: 'low' | 'medium' | 'high';
  /** Kontextregeln, z.B. 'immer diese 5 Symbole einbeziehen'. */
  contextRules: ChTemplateContextRule[];
  createdAt: number;
  updatedAt: number;
}

export type ChTemplateContextRule =
  | { type: 'always_include_symbol'; symbolId: string }
  | { type: 'always_include_file'; filePath: string }
  | { type: 'always_include_domain'; domain: string }
  | { type: 'never_include'; pattern: string };

// ─────────────────────────────────────────────────────────────────────────────
// Hub/Worker Topology & Internals
// ─────────────────────────────────────────────────────────────────────────────

export interface ChHubInstanceReadModel {
  id: string;
  url: string;
  status: 'online' | 'offline' | 'degraded';
  version: string;
  startedAt: number;
}

export interface ChWorkerInstanceReadModel {
  id: string;
  hubId: string;
  /** Typ des Workers (ananta-worker, opencode-runner, sgpt-runner, ...). */
  type: string;
  cliBackend: ChCliBackend;
  /** Modell das der Worker nutzt (oder 'deterministic'). */
  model: string;
  llmProvider: ChLlmProvider;
  capabilities: string[];
  health: 'healthy' | 'degraded' | 'unhealthy' | 'unknown';
  boundary: 'local-only' | 'cloud-allowed' | 'remote' | 'unknown';
  registeredAt: number;
  lastHeartbeatAt: number | null;
}

export interface ChTopologyReadModel {
  hubs: ChHubInstanceReadModel[];
  workers: ChWorkerInstanceReadModel[];
  /** Verbindungen Hub -> Worker. */
  connections: ChTopologyConnection[];
  /** Effektive Routing-Regeln. */
  routingRules: ChRoutingRuleReadModel[];
  /** Aktive Test-Layer. */
  activeLayers: ChTestLayerReadModel[];
}

export interface ChTopologyConnection {
  hubId: string;
  workerId: string;
  transport: 'http' | 'https' | 'stdio' | 'websocket' | 'unknown';
  status: 'connected' | 'disconnected' | 'degraded';
}

export interface ChRoutingRuleReadModel {
  id: string;
  description: string;
  match: Record<string, unknown>;
  selectedBackend: ChCliBackend;
  selectedModel: string;
  priority: number;
}

export interface ChTestLayerReadModel {
  id: string;
  name: string;
  /** Reihenfolge (niedrig zuerst). */
  order: number;
  enabled: boolean;
  parameters: Record<string, unknown>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Policy
// ─────────────────────────────────────────────────────────────────────────────

export interface ChPolicySnapshotReadModel {
  id: string;
  policyVersion: string;
  riskLevel: 'low' | 'medium' | 'high';
  allowedTools: string[];
  deniedTools: string[];
  allowedPaths: string[];
  deniedPaths: string[];
  /** Patterns fuer sensitive files. */
  sensitiveFilePatterns: string[];
  cloudAllowed: boolean;
  runtimeBoundary: 'local-only' | 'cloud-allowed' | 'remote' | 'unknown';
  requiresHumanApproval: boolean;
  approvalReason?: string | null;
  createdAt: number;
}

export interface ChPolicyDecisionReadModel {
  id: string;
  decision: 'allow' | 'deny' | 'require_approval';
  decisionType: string;
  reason: string;
  matchedRuleIds: string[];
  createdAt: number;
  actionId?: string;
  toolCallId?: string;
}

export interface ChPolicyUpdateRequest {
  /** Pfade die explizit erlaubt sind. */
  allowedPaths?: string[];
  /** Pfade die explizit verboten sind. */
  deniedPaths?: string[];
  /** Patterns fuer sensitive files. */
  sensitiveFilePatterns?: string[];
  /** Welche Profile erlaubt sind. */
  allowedProfileIds?: string[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Sensitive File Handling
// ─────────────────────────────────────────────────────────────────────────────

/** Default-Patterns fuer sensitive files (koennen ueber Policy ueberschrieben werden). */
export const DEFAULT_SENSITIVE_FILE_PATTERNS: readonly string[] = Object.freeze([
  '.env',
  '.env.*',
  '**/secrets/**',
  '**/*.pem',
  '**/*.key',
  '**/id_rsa',
  '**/id_ed25519',
  '**/.ssh/**',
  '**/credentials*',
  '**/service-account*.json',
]);

export interface ChSensitiveFileDecision {
  filePath: string;
  matchedPattern: string | null;
  decision: 'auto-exclude' | 'requires-confirmation';
}

// ─────────────────────────────────────────────────────────────────────────────
// Write Mode (Read-only default)
// ─────────────────────────────────────────────────────────────────────────────

export type ChWriteMode = 'read-only' | 'write-armed';

/** Konfigurierbares Timeout fuer write-mode in Millisekunden. Default: 15min. */
export const DEFAULT_WRITE_MODE_TIMEOUT_MS = 15 * 60 * 1000;

// ─────────────────────────────────────────────────────────────────────────────
// Generic API result wrapper
// ─────────────────────────────────────────────────────────────────────────────

/** Strukturierte Fehler von CodeHug-Services. */
export type ChServiceErrorCode =
  | 'network_error'
  | 'timeout'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'policy_violation'
  | 'validation_error'
  | 'backend_error'
  | 'unknown';

export class ChServiceError extends Error {
  constructor(
    public readonly code: ChServiceErrorCode,
    message: string,
    public readonly cause?: unknown,
  ) {
    super(message);
    this.name = 'ChServiceError';
  }
}

export interface ChPaginated<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

// ────────────────────────────────────────────────────────────────────────────
// CH-005: Refactoring
// ────────────────────────────────────────────────────────────────────────────

export type ChRefactorKind =
  | 'rename_symbol'
  | 'extract_function'
  | 'inline_function'
  | 'move_to_module'
  | 'convert_type'
  | 'wrap_with_helper'
  | 'optimize_imports';

export interface ChRefactorProposalReadModel {
  id: string;
  kind: ChRefactorKind;
  title: string;
  description: string;
  /** betroffene Dateien, Pfade relativ zum Workspace-Root */
  affectedFiles: string[];
  /** geaenderte Symbole / Bereiche */
  affectedSymbols: string[];
  /** deterministisch (rule-based) oder LLM-generiert */
  generatedBy: 'deterministic' | 'llm';
  /** Confidence (0-1), deterministische Vorschlaege >=0.9 */
  confidence: number;
  /** Layer-Kontext: nur sichtbar, wenn Layer-Set matcht */
  layerSet?: string[];
  createdAt: number;
  status: 'open' | 'previewed' | 'applied' | 'dismissed' | 'failed';
}

export interface ChRefactorProposalInput {
  workspacePath: string;
  /** optional — einschraenken auf bestimmte Dateien */
  files?: string[];
  /** Refactoring-Kategorie (oder 'auto' = alle passenden) */
  kind?: ChRefactorKind | 'auto';
  /** Kontext-Paket (Symbol-Selektor o.ae.) */
  selector?: {
    symbolId?: string;
    functionName?: string;
    modulePath?: string;
  };
}

export interface ChRefactorDiffHunk {
  filePath: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  unified: string;
}

export interface ChRefactorDiffReadModel {
  proposalId: string;
  hunks: ChRefactorDiffHunk[];
  /** syntaktische Validierung des Ergebnisses (deterministisch) */
  validation: {
    syntaxOk: boolean;
    typeCheckOk: boolean;
    linterOk: boolean;
    diagnostics: { severity: 'info' | 'warning' | 'error'; message: string; line?: number }[];
  };
  generatedAt: number;
}

export interface ChRefactorApplyResult {
  proposalId: string;
  status: 'applied' | 'failed' | 'rolled_back';
  appliedFiles: string[];
  testGate: {
    ran: boolean;
    passed: boolean;
    failedLayer?: string;
    diagnostics: string[];
  };
  message: string;
}

// ────────────────────────────────────────────────────────────────────────────
// CH-006: Custom Agent
// ────────────────────────────────────────────────────────────────────────────

export interface ChAgentDefinitionInput {
  name: string;
  description: string;
  /** System prompt */
  systemPrompt: string;
  /** Optional vorgegebener Tool-Set */
  tools?: string[];
  /** Layer-Set (welche Test-Layer fuer Runs) */
  layerSet?: string[];
  /** Bevorzugtes Backend (oder 'auto' = Router entscheidet) */
  preferredBackend?: 'sgpt' | 'opencode' | 'codex' | 'claude_code' | 'aider' | 'mistral' | 'auto';
  /** Bevorzugtes Modell */
  preferredModel?: string;
  /** Capabilities / erlaubte Aktionen (read, write, exec, network, …) */
  capabilities?: ('read' | 'write' | 'exec' | 'network' | 'policy-vote')[];
  /** Temperatur 0-1 */
  temperature?: number;
  /** Maximale Tokens */
  maxTokens?: number;
  /** Tags */
  tags?: string[];
}

export interface ChAgentDefinitionReadModel extends ChAgentDefinitionInput {
  id: string;
  createdAt: number;
  updatedAt: number;
  runCount: number;
  lastRunAt: number | null;
  author: string;
}

// ────────────────────────────────────────────────────────────────────────────
// CH-007: Search + Explain
// ────────────────────────────────────────────────────────────────────────────

export type ChSearchMode = 'fulltext' | 'symbol' | 'fuzzy' | 'hybrid';

export interface ChSearchRequest {
  query: string;
  mode: ChSearchMode;
  workspacePath?: string;
  /** Optional — eingeschraenkter Symbol-Typ */
  symbolKind?: 'function' | 'class' | 'method' | 'variable' | 'module';
  /** Layer-Filter (CH-013-004) */
  layerSet?: string[];
  /** Treffer-Limit */
  limit?: number;
}

export interface ChSearchResult {
  symbolId: string;
  name: string;
  kind: 'function' | 'class' | 'method' | 'variable' | 'module' | 'file';
  filePath: string;
  line: number;
  /** Match-Snippet (umgebender Code) */
  snippet: string;
  /** Welcher Match-Modus (fulltext/symbol/fuzzy) */
  matchMode: ChSearchMode;
  score: number;
  /** Tags (Layer-Zugehoerigkeit) */
  tags?: string[];
}

export type ChExplanationKind = 'heuristic' | 'llm' | 'hybrid';

export interface ChExplanationReadModel {
  symbolId: string;
  kind: ChExplanationKind;
  summary: string;
  details: string[];
  relatedSymbols: string[];
  llmEnhanced: boolean;
  /** Zeitpunkt der Erstellung */
  generatedAt: number;
}

export interface ChSymbolCallerOrCallee {
  id: string;
  name: string;
  filePath: string;
  line: number;
  kind: 'function' | 'class' | 'method' | 'variable' | 'module';
}

export interface ChSymbolDetailReadModel {
  id: string;
  name: string;
  kind: 'function' | 'class' | 'method' | 'variable' | 'module';
  filePath: string;
  line: number;
  signature: string;
  documentation: string;
  visibility: 'public' | 'private' | 'protected' | 'package';
  isAsync: boolean;
  isStatic: boolean;
  decorators: string[];
  parentSymbolId?: string;
  /** Caller (was ruft MICH auf) */
  callers: ChSymbolCallerOrCallee[];
  /** Callee (was rufe ICH auf) */
  callees: ChSymbolCallerOrCallee[];
  /** Tests, die das Symbol referenzieren */
  referencedByTests: string[];
  /** Tags / Layer-Zugehoerigkeit */
  tags: string[];
  /** Sprache (typescript, python, …) */
  language: string;
}

// ────────────────────────────────────────────────────────────────────────────
// CH-010: Security / Policy — Audit + Risk
// ────────────────────────────────────────────────────────────────────────────

export type ChAuditKind = 'policy-check' | 'write-armed' | 'write-disarmed' | 'tool-call' | 'approval' | 'denial';

export interface ChAuditEntry {
  id: string;
  ts: number;
  kind: ChAuditKind;
  action: string;
  decision?: 'allow' | 'deny' | 'require_approval' | 'warn';
  reason?: string;
  actor?: string;
  metadata?: Record<string, unknown>;
}

export type ChToolRiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type ChToolRiskRecommendation = 'allow' | 'warn' | 'require_approval' | 'deny';

export interface ChToolRiskAssessment {
  toolName: string;
  level: ChToolRiskLevel;
  reasons: string[];
  recommendation: ChToolRiskRecommendation;
  assessedAt: number;
}

// ────────────────────────────────────────────────────────────────────────────
// CH-011: Persistenz / Wiederverwendung
// ────────────────────────────────────────────────────────────────────────────

export interface ChWorkspaceInput {
  name: string;
  description?: string;
  /** Pfad zum Quell-Verzeichnis (oder leer fuer konfigurationsfrei) */
  rootPath?: string;
  /** Default-Layer-Set fuer Runs */
  layerSet?: string[];
  /** Optionale Tags */
  tags?: string[];
}

export interface ChWorkspaceReadModel extends ChWorkspaceInput {
  id: string;
  createdAt: number;
  updatedAt: number;
  /** Letzte Activity (z.B. letzter Run) */
  lastActivityAt: number | null;
  /** Anzahl enthaltener Snapshots */
  snapshotCount: number;
  /** Ersteller / Owner */
  owner: string;
}

export interface ChContextSnapshotInput {
  workspaceId: string;
  name: string;
  /** Beschreibung des Snapshot-Zwecks */
  purpose?: string;
  /** Symbol-IDs im Kontext */
  symbolIds: string[];
  /** Datei-IDs (relativ) */
  fileIds: string[];
  /** Layer-Set zum Zeitpunkt der Erstellung */
  layerSet: string[];
  /** Optional: Bezugnahme auf einen Run */
  sourceRunId?: string;
  /** Tags */
  tags?: string[];
}

export interface ChContextSnapshotReadModel extends ChContextSnapshotInput {
  id: string;
  createdAt: number;
  /** Version des Snapshots (auto-increment bei Aktualisierung) */
  version: number;
  /** Wer hat es erstellt */
  author: string;
}