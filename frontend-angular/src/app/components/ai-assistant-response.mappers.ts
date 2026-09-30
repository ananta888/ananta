import { ChatMessage, CliBackend, ContextSource } from './ai-assistant.types';

/**
 * Pure mappers for hub responses consumed by the assistant: CLI backend
 * discovery, SGPT routing/context metadata and tool execution summaries.
 */

const KNOWN_CLI_BACKENDS: CliBackend[] = [
  'sgpt', 'codex', 'opencode', 'claude_code', 'aider', 'mistral_code',
  'qwen_code', 'gemini_cli', 'copilot_cli', 'cline', 'kilo_code',
];

export function isCliBackend(value: string): value is CliBackend {
  return (['auto', ...KNOWN_CLI_BACKENDS] as string[]).includes(value);
}

/** 'auto' followed by every known backend the hub reports as supported. */
export function toAvailableCliBackends(supportedBackends: Record<string, unknown> | undefined | null): CliBackend[] {
  const supported = Object.keys(supportedBackends || {});
  return ['auto', ...KNOWN_CLI_BACKENDS.filter(backend => supported.includes(backend))];
}

export function toRoutingMeta(routing: any): ChatMessage['routing'] {
  return {
    requestedBackend: routing.requested_backend,
    effectiveBackend: routing.effective_backend,
    reason: routing.reason,
    policyVersion: routing.policy_version,
  };
}

export function mergeContextMeta(current: ChatMessage['contextMeta'], ctx: any, chunks: any[]): ChatMessage['contextMeta'] {
  return {
    ...(current || {}),
    policy_version: ctx?.policy_version || current?.policy_version,
    chunk_count: typeof ctx?.chunk_count === 'number' ? ctx.chunk_count : chunks.length,
    token_estimate: typeof ctx?.token_estimate === 'number' ? ctx.token_estimate : current?.token_estimate,
    strategy: ctx?.strategy || current?.strategy,
    explainability: ctx?.explainability || current?.explainability,
  };
}

export function toContextSources(chunks: any[]): ContextSource[] {
  return chunks.map((c: any) => ({
    engine: c.engine,
    source: c.source,
    score: c.score,
    recordKind: c?.metadata?.record_kind,
    artifactId: c?.metadata?.artifact_id,
    knowledgeIndexId: c?.metadata?.knowledge_index_id,
    collectionNames: Array.isArray(c?.metadata?.collection_names) ? c.metadata.collection_names : [],
  }));
}

export function formatToolResults(toolResults: any[]): string {
  return toolResults.length
    ? `\n\nTool results:\n${toolResults.map((tr: any) => `- ${tr?.tool || 'tool'}: ${tr?.success ? 'ok' : 'failed'}${tr?.error ? ` (${tr.error})` : ''}`).join('\n')}`
    : '';
}

export function formatExecutionOutput(r: { stdout?: string; stderr?: string }): string {
  let resultMsg = '### Execution Output\n';
  if (r.stdout) resultMsg += '```text\n' + r.stdout + '\n```';
  if (r.stderr) resultMsg += '\n### Errors\n```text\n' + r.stderr + '\n```';
  if (!r.stdout && !r.stderr) resultMsg = 'Command executed without output.';
  return resultMsg;
}
