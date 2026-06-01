export type CcTaskStatus = 'backlog' | 'proposed' | 'running' | 'blocked' | 'review' | 'verified' | 'done' | 'failed';
export type CcRiskLevel = 'low' | 'medium' | 'high' | 'critical';

export interface CcVerificationSummary {
  status: 'not_run' | 'running' | 'passed' | 'failed' | 'partial' | 'skipped';
  testCount: number;
  passedCount: number;
  failedCount: number;
}

export interface CcTaskCard {
  id: string;
  title: string;
  description: string;
  status: CcTaskStatus;
  riskLevel: CcRiskLevel;
  assignedWorkerId: string | null;
  preferredModel: string | null;
  artifactIds: string[];
  verificationSummary: CcVerificationSummary | null;
}

export interface CcPolicySnapshot {
  riskLevel: CcRiskLevel;
  allowedTools: string[];
  deniedTools: string[];
  allowedPaths: string[];
  deniedPaths: string[];
  requiresHumanApproval: boolean;
  approvalReason: string | null;
  policyVersion: string;
}

export interface CcToolCall {
  id: string;
  toolName: string;
  status: 'proposed' | 'allowed' | 'denied' | 'running' | 'completed' | 'failed';
  startedAt: string | null;
  finishedAt: string | null;
}

export interface CcAgentSession {
  id: string;
  taskId: string | null;
  workerId: string;
  workerType: 'ananta-worker' | 'opencode' | 'hermes' | 'codex' | 'claude-code' | 'custom';
  model: string;
  runtime: 'local' | 'docker' | 'remote' | 'cloud';
  status: 'idle' | 'proposed' | 'running' | 'waiting_for_approval' | 'blocked' | 'review' | 'done' | 'failed' | 'cancelled';
  policySnapshot: CcPolicySnapshot;
  toolCalls: CcToolCall[];
  updatedAt: string;
}
