import { Injectable } from '@angular/core';
import { VoxtralOfflineService } from './voxtral-offline.service';

export type AgentCapability = 'text_generation' | 'speech_to_text' | 'embedding';

export interface AgentRuntimeRequest {
  capability: AgentCapability;
  prompt?: string;
  audioPath?: string;
  modelPath?: string;
  runnerPath?: string;
  maxContextChars?: number;
  allowRemoteFallback?: boolean;
  requestedTools?: string[];
}

export interface AgentRuntimeResponse {
  route: 'local' | 'remote';
  output: string;
  usedFallback: boolean;
}

export interface RemoteFallbackExecutor {
  execute(req: AgentRuntimeRequest): Promise<string>;
}

/**
 * Hub-facing adapter for mobile runtime selection.
 * Keeps worker execution local-first and blocks direct tool execution through model prompts.
 */
@Injectable({ providedIn: 'root' })
export class MobileAgentRuntimeAdapterService {
  private remoteFallbackExecutor?: RemoteFallbackExecutor;

  constructor(private readonly voxtral: VoxtralOfflineService) {}

  configureRemoteFallback(executor: RemoteFallbackExecutor | undefined): void {
    this.remoteFallbackExecutor = executor;
  }

  async execute(request: AgentRuntimeRequest): Promise<AgentRuntimeResponse> {
    this.assertNoDirectToolExecution(request.requestedTools || []);

    const normalizedPrompt = this.limitAndSummarizeContext(
      String(request.prompt || ''),
      request.maxContextChars ?? 6000,
    );

    if (request.capability === 'speech_to_text') {
      if (this.isSpeechLocallyPossible(request)) {
        const result = await this.voxtral.transcribe(
          String(request.audioPath || ''),
          String(request.modelPath || ''),
          String(request.runnerPath || ''),
        );
        return { route: 'local', output: result.transcript || '', usedFallback: false };
      }
      return this.executeRemoteFallbackIfAllowed(request);
    }

    if (request.capability === 'text_generation') {
      if (normalizedPrompt.trim().length > 0) {
        // Local text runtime is currently plugin-scaffolded; this preserves the adapter contract.
        return { route: 'local', output: normalizedPrompt, usedFallback: false };
      }
      return this.executeRemoteFallbackIfAllowed(request);
    }

    return this.executeRemoteFallbackIfAllowed(request);
  }

  private isSpeechLocallyPossible(request: AgentRuntimeRequest): boolean {
    return Boolean(
      request.audioPath && request.modelPath && request.runnerPath,
    );
  }

  private async executeRemoteFallbackIfAllowed(request: AgentRuntimeRequest): Promise<AgentRuntimeResponse> {
    if (!request.allowRemoteFallback || !this.remoteFallbackExecutor) {
      throw new Error('No local route available and remote fallback is disabled.');
    }
    const output = await this.remoteFallbackExecutor.execute(request);
    return { route: 'remote', output, usedFallback: true };
  }

  private limitAndSummarizeContext(prompt: string, maxChars: number): string {
    const trimmed = prompt.trim();
    this.assertPromptSafety(trimmed);
    if (trimmed.length <= maxChars) return trimmed;
    const head = trimmed.slice(0, Math.max(0, maxChars - 180));
    const tail = trimmed.slice(-120);
    return `${head}\n\n[context summary]\n...\n${tail}`;
  }

  private assertNoDirectToolExecution(requestedTools: string[]): void {
    if (!Array.isArray(requestedTools) || requestedTools.length === 0) return;
    throw new Error('Direct tool execution from model requests is blocked by policy.');
  }

  private assertPromptSafety(prompt: string): void {
    const lowered = prompt.toLowerCase();
    const blockedSignals = [
      'ignore previous instructions',
      'bypass policy',
      'disable guardrail',
      'execute shell',
      'run command',
    ];
    if (blockedSignals.some((item) => lowered.includes(item))) {
      throw new Error('Prompt blocked by injection guardrails.');
    }
  }
}
