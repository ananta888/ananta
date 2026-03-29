export interface ContextMeta {
  policy_version?: string;
  chunk_count?: number;
  token_estimate?: number;
  strategy?: any;
  explainability?: {
    engines?: string[];
    artifact_ids?: string[];
    knowledge_index_ids?: string[];
    chunk_types?: string[];
    collection_ids?: string[];
    collection_names?: string[];
    source_count?: number;
  };
}

export interface ContextSource {
  engine: string;
  source: string;
  score?: number;
  recordKind?: string;
  artifactId?: string;
  knowledgeIndexId?: string;
  collectionNames?: string[];
  preview?: string;
  previewLoading?: boolean;
  previewError?: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  requiresConfirmation?: boolean;
  toolCalls?: any[];
  confirmationText?: string;
  pendingPrompt?: string;
  sgptCommand?: string;
  cliBackendUsed?: string;
  contextMeta?: ContextMeta;
  contextSources?: ContextSource[];
  routing?: {
    requestedBackend?: string;
    effectiveBackend?: string;
    reason?: string;
    policyVersion?: string;
  };
  planRisk?: { level: 'low' | 'medium' | 'high'; reason: string };
  recoverableError?: boolean;
}

export interface AssistantRuntimeContext {
  route: string;
  selectedAgentName?: string;
  userRole?: string;
  userName?: string;
  agents: Array<{ name: string; role?: string; url: string }>;
  teamsCount: number;
  templatesCount: number;
  templatesSummary: Array<{ name: string; description?: string }>;
  settingsSummary?: any;
  editableSettings: Array<{ key: string; path?: string; type?: string; endpoint?: string }>;
  automationSummary?: any;
  hasConfig: boolean;
  configSnapshot?: any;
}

export type CliBackend = 'auto' | 'sgpt' | 'codex' | 'opencode' | 'aider' | 'mistral_code';
