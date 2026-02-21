export interface ContextMeta {
  policy_version?: string;
  chunk_count?: number;
  token_estimate?: number;
  strategy?: any;
}

export interface ContextSource {
  engine: string;
  source: string;
  score?: number;
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
  hasConfig: boolean;
  configSnapshot?: any;
}

export type CliBackend = 'auto' | 'sgpt' | 'opencode' | 'aider' | 'mistral_code';
