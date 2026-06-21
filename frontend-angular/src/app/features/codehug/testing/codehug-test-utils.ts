/**
 * CodeHug Test Utilities — shared mock factories and test helpers.
 *
 * Alle Factories liefern minimal gültige Objekte; optionale Overrides
 * ermöglichen gezieltes Überschreiben einzelner Felder in Tests.
 */

import {
  ChProjectReadModel,
  ChFileReadModel,
  ChSymbolReadModel,
  ChContextPackageReadModel,
  ChAgentRunReadModel,
  ChAgentStepReadModel,
  ChPolicySnapshotReadModel,
  ChContextSuggestionReadModel,
  ChAgentProfileReadModel,
  ChTaskTemplateReadModel,
  ChHubInstanceReadModel,
  ChWorkerInstanceReadModel,
  ChTopologyReadModel,
  ChRefactorProposalReadModel,
  ChAuditEntry,
} from '../models/codehug.models';

let _seq = 0;
const uid = (prefix = 'id') => `${prefix}-${++_seq}-${Math.random().toString(36).slice(2, 6)}`;

export function mockProject(overrides: Partial<ChProjectReadModel> = {}): ChProjectReadModel {
  return {
    id: uid('proj'),
    name: 'test-project',
    rootPath: '/workspace/test',
    languageBreakdown: { typescript: 80, scss: 20 },
    frameworkSignals: ['angular', 'rxjs'],
    moduleCount: 5,
    fileCount: 42,
    symbolCount: 150,
    lastIndexedAt: Date.now() - 60_000,
    indexStatus: 'complete',
    ...overrides,
  };
}

export function mockFile(overrides: Partial<ChFileReadModel> = {}): ChFileReadModel {
  return {
    path: `src/app/features/test-${uid()}.ts`,
    language: 'typescript',
    sizeBytes: 1024,
    lastModified: Date.now() - 3600_000,
    symbolIds: [],
    isSensitive: false,
    ...overrides,
  };
}

export function mockSymbol(overrides: Partial<ChSymbolReadModel> = {}): ChSymbolReadModel {
  return {
    id: uid('sym'),
    name: 'TestFunction',
    qualifiedName: 'src/test.ts::TestFunction',
    kind: 'function',
    filePath: 'src/app/test.ts',
    lineStart: 10,
    lineEnd: 25,
    visibility: 'public',
    ...overrides,
  };
}

export function mockContextPackage(overrides: Partial<ChContextPackageReadModel> = {}): ChContextPackageReadModel {
  return {
    id: uid('pkg'),
    projectId: uid('proj'),
    name: 'test-context-package',
    version: 1,
    createdAt: Date.now() - 120_000,
    updatedAt: Date.now() - 60_000,
    filePaths: ['src/app/test.ts'],
    symbolIds: ['sym-001'],
    reasons: { 'src/app/test.ts': 'relevant to task' },
    estimatedTokenCount: 2500,
    ...overrides,
  };
}

export function mockAgentStep(overrides: Partial<ChAgentStepReadModel> = {}): ChAgentStepReadModel {
  return {
    id: uid('step'),
    index: 0,
    phase: 'plan',
    title: 'Planning',
    startedAt: Date.now() - 5000,
    finishedAt: Date.now() - 2000,
    durationMs: 3000,
    status: 'succeeded',
    ...overrides,
  };
}

export function mockAgentRun(overrides: Partial<ChAgentRunReadModel> = {}): ChAgentRunReadModel {
  return {
    id: uid('run'),
    status: 'succeeded',
    projectId: uid('proj'),
    profileId: uid('profile'),
    startedAt: Date.now() - 30_000,
    finishedAt: Date.now() - 5_000,
    durationMs: 25_000,
    writeArmed: false,
    steps: [mockAgentStep()],
    actualCliBackend: 'sgpt',
    actualModel: 'ollama/codellama',
    actualProvider: 'ollama',
    deterministicStepCount: 2,
    llmStepCount: 3,
    routingReason: 'default backend for code tasks',
    policySnapshotId: uid('policy'),
    warnings: [],
    ...overrides,
  };
}

export function mockPolicySnapshot(overrides: Partial<ChPolicySnapshotReadModel> = {}): ChPolicySnapshotReadModel {
  return {
    id: uid('policy'),
    policyVersion: '1.0.0',
    riskLevel: 'low',
    allowedTools: ['read_file', 'search_symbols'],
    deniedTools: ['shell_exec'],
    allowedPaths: ['src/'],
    deniedPaths: ['.env', 'secrets/'],
    sensitiveFilePatterns: ['.env', '.env.*', '**/*.pem', '**/credentials*'],
    cloudAllowed: false,
    runtimeBoundary: 'local-only',
    requiresHumanApproval: false,
    approvalReason: null,
    createdAt: Date.now() - 86_400_000,
    ...overrides,
  };
}

export function mockSuggestion(overrides: Partial<ChContextSuggestionReadModel> = {}): ChContextSuggestionReadModel {
  return {
    filePath: 'src/app/test.ts',
    reason: 'Highly relevant to task',
    relevanceScore: 0.92,
    source: 'resolve_context',
    ...overrides,
  };
}

export function mockAgentProfile(overrides: Partial<ChAgentProfileReadModel> = {}): ChAgentProfileReadModel {
  return {
    id: uid('profile'),
    name: 'Code Review Profile',
    purpose: 'Read-only code analysis and review',
    capabilities: {
      canRead: true,
      canWrite: false,
      canExecute: false,
      canNetwork: false,
      canUseTools: true,
      requiresHumanApproval: false,
    },
    model: 'ollama/codellama',
    cliBackend: 'sgpt',
    llmProvider: 'ollama',
    allowedTools: ['read_file', 'search_symbols', 'list_dir'],
    allowedPaths: ['src/'],
    maxContextTokens: 32000,
    maxRuntimeMs: 120_000,
    ...overrides,
  };
}

export function mockTaskTemplate(overrides: Partial<ChTaskTemplateReadModel> = {}): ChTaskTemplateReadModel {
  return {
    id: uid('tmpl'),
    name: 'Refactor Auth Module',
    taskDescription: 'Refactor the auth module to use the new session API',
    defaultRiskLevel: 'medium',
    contextRules: [
      { type: 'always_include_file', filePath: 'src/app/auth.service.ts' },
    ],
    createdAt: Date.now() - 86_400_000,
    updatedAt: Date.now() - 3600_000,
    ...overrides,
  };
}

export function mockHubInstance(overrides: Partial<ChHubInstanceReadModel> = {}): ChHubInstanceReadModel {
  return {
    id: uid('hub'),
    url: 'http://localhost:8765',
    status: 'online',
    version: '1.0.0',
    startedAt: Date.now() - 3_600_000,
    ...overrides,
  };
}

export function mockWorkerInstance(overrides: Partial<ChWorkerInstanceReadModel> = {}): ChWorkerInstanceReadModel {
  return {
    id: uid('worker'),
    hubId: uid('hub'),
    type: 'ananta-worker',
    cliBackend: 'sgpt',
    model: 'ollama/codellama',
    llmProvider: 'ollama',
    capabilities: ['read', 'search'],
    health: 'healthy',
    boundary: 'local-only',
    registeredAt: Date.now() - 1_800_000,
    lastHeartbeatAt: Date.now() - 5000,
    ...overrides,
  };
}

export function mockTopology(overrides: Partial<ChTopologyReadModel> = {}): ChTopologyReadModel {
  const hub = mockHubInstance();
  const worker = mockWorkerInstance({ hubId: hub.id });
  return {
    hubs: [hub],
    workers: [worker],
    connections: [{ hubId: hub.id, workerId: worker.id, transport: 'http', status: 'connected' }],
    routingRules: [],
    activeLayers: [],
    ...overrides,
  };
}

export function mockRefactorProposal(overrides: Partial<ChRefactorProposalReadModel> = {}): ChRefactorProposalReadModel {
  return {
    id: uid('refactor'),
    kind: 'extract_function',
    title: 'Extract validation logic',
    description: 'The validation logic in processUser() can be extracted into a separate validateUser() function.',
    affectedFiles: ['src/app/user.service.ts'],
    affectedSymbols: ['processUser'],
    generatedBy: 'llm',
    confidence: 0.87,
    createdAt: Date.now() - 10_000,
    status: 'open',
    ...overrides,
  };
}

export function mockAuditEntry(overrides: Partial<ChAuditEntry> = {}): ChAuditEntry {
  return {
    id: uid('audit'),
    ts: Date.now() - 5000,
    kind: 'policy-check',
    action: 'read_file',
    decision: 'allow',
    reason: 'Path within allowed scope',
    ...overrides,
  };
}
