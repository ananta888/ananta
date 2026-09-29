import { normalizeModelOverrideMapValue } from './settings-config.helpers';
import { parseCommaListValue } from './settings-provider-runtime.helpers';

/**
 * Pure mapping between the settings page's small editors (per-agent LLM drafts,
 * benchmark config, quality gates, JSON text editors) and the hub config payloads.
 */

// ---- per-agent LLM drafts -------------------------------------------------

export interface AgentLlmDraft {
  provider: string;
  model: string;
  temperature: number;
  context_limit: number;
  api_key_profile: string;
}

export function createDefaultAgentLlmDraft(): AgentLlmDraft {
  return {
    provider: 'lmstudio',
    model: '',
    temperature: 0.2,
    context_limit: 4096,
    api_key_profile: ''
  };
}

/** Draft fields derived from an agent's own config (merged over the existing draft by the caller). */
export function agentLlmDraftFromConfig(cfg: any): AgentLlmDraft {
  const llm = (cfg && cfg.llm_config) ? cfg.llm_config : {};
  const temperature = Number(llm.temperature ?? 0.2);
  const contextLimit = Number(llm.context_limit ?? 4096);
  return {
    provider: String(llm.provider || cfg?.default_provider || 'lmstudio'),
    model: String(llm.model || cfg?.default_model || ''),
    temperature: Number.isFinite(temperature) ? temperature : 0.2,
    context_limit: Number.isFinite(contextLimit) ? contextLimit : 4096,
    api_key_profile: String(llm.api_key_profile || '')
  };
}

/** Returns a user-facing error message, or '' when the draft is valid. */
export function agentLlmDraftError(agentName: string, draft: any): string {
  const temp = Number(draft.temperature);
  const ctx = Number(draft.context_limit);
  if (!Number.isFinite(temp) || temp < 0 || temp > 2) return `Temperature ungueltig fuer Agent ${agentName}`;
  if (!Number.isFinite(ctx) || ctx < 256) return `Context Limit ungueltig fuer Agent ${agentName}`;
  return '';
}

/** `temperature`/`contextLimit` are the values validated when saving started. */
export function buildAgentLlmConfigPayload(currentCfg: any, draft: any, temperature: number, contextLimit: number): any {
  const current = currentCfg && typeof currentCfg === 'object' ? currentCfg : {};
  return {
    ...current,
    llm_config: {
      ...(current.llm_config || {}),
      provider: String(draft.provider || 'lmstudio'),
      model: String(draft.model || ''),
      temperature,
      context_limit: Math.round(contextLimit),
      api_key_profile: String(draft.api_key_profile || '')
    }
  };
}

// ---- JSON text editors ----------------------------------------------------

/** Parses a JSON editor whose root must be an object; throws on invalid input. */
export function parseJsonObjectText(text: string): any {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Root muss ein Objekt sein.');
  }
  return parsed;
}

/** Parses a model override editor (empty text = {}); throws on invalid input. */
export function parseModelOverrideText(text: string): Record<string, string> {
  const raw = String(text || '').trim();
  return normalizeModelOverrideMapValue(raw ? parseJsonObjectText(raw) : {});
}

export function invalidJsonMessage(error: unknown): string {
  return `Ungueltiges JSON: ${error instanceof Error ? error.message : String(error)}`;
}

// ---- benchmark config -----------------------------------------------------

const BENCHMARK_PROVIDER_KEYS = new Set(['proposal_backend', 'routing_effective_backend', 'llm_config_provider', 'default_provider', 'provider']);
const BENCHMARK_MODEL_KEYS = new Set(['proposal_model', 'llm_config_model', 'default_model', 'model']);

export interface BenchmarkEditorState {
  retentionDays: number;
  retentionSamples: number;
  providerOrderText: string;
  modelOrderText: string;
}

export function benchmarkEditorStateFromConfig(cfg: any): BenchmarkEditorState {
  const retention = cfg?.retention || {};
  const precedence = cfg?.identity_precedence || {};
  const providerOrder = Array.isArray(precedence.provider_order) ? precedence.provider_order : [];
  const modelOrder = Array.isArray(precedence.model_order) ? precedence.model_order : [];
  return {
    retentionDays: Number(retention.max_days || 90),
    retentionSamples: Number(retention.max_samples || 2000),
    providerOrderText: providerOrder.join(', '),
    modelOrderText: modelOrder.join(', '),
  };
}

export function formatBenchmarkOrderText(text: string): string {
  const arr = parseCommaListValue(text);
  return Array.isArray(arr) && arr.length ? arr.join(' -> ') : '-';
}

/** Returns '' when both precedence lists only use known keys. */
export function benchmarkOrderValidationError(providerOrder: string[], modelOrder: string[]): string {
  const invalidProviderKeys = providerOrder.filter((k) => !BENCHMARK_PROVIDER_KEYS.has(k));
  const invalidModelKeys = modelOrder.filter((k) => !BENCHMARK_MODEL_KEYS.has(k));
  if (!invalidProviderKeys.length && !invalidModelKeys.length) return '';
  return [
    invalidProviderKeys.length ? `ungueltige provider_order keys: ${invalidProviderKeys.join(', ')}` : '',
    invalidModelKeys.length ? `ungueltige model_order keys: ${invalidModelKeys.join(', ')}` : '',
  ]
    .filter(Boolean)
    .join(' | ');
}

export function clampBenchmarkRetentionDays(value: any): number {
  return Math.max(1, Math.min(3650, Number(value || 90)));
}

export function clampBenchmarkRetentionSamples(value: any): number {
  return Math.max(50, Math.min(50000, Number(value || 2000)));
}

export function buildBenchmarkConfigPayload(days: number, samples: number, providerOrder: string[], modelOrder: string[]): any {
  return {
    benchmark_retention: {
      max_days: days,
      max_samples: samples,
    },
    benchmark_identity_precedence: {
      provider_order: providerOrder,
      model_order: modelOrder,
    },
  };
}

// ---- quality gates --------------------------------------------------------

export interface QualityGateEditorState {
  enabled: boolean;
  autopilotEnforce: boolean;
  minOutputChars: number;
  codingKeywordsText: string;
  markersText: string;
}

/** Maps hub quality_gates onto the editor; list fields keep `previous` when absent. */
export function qualityGateEditorStateFromConfig(cfg: any, previous: QualityGateEditorState): QualityGateEditorState {
  const qg = (cfg && cfg.quality_gates) ? cfg.quality_gates : {};
  return {
    enabled: qg.enabled !== false,
    autopilotEnforce: qg.autopilot_enforce !== false,
    minOutputChars: Number(qg.min_output_chars || 8),
    codingKeywordsText: Array.isArray(qg.coding_keywords) ? qg.coding_keywords.join(', ') : previous.codingKeywordsText,
    markersText: Array.isArray(qg.required_output_markers_for_coding)
      ? qg.required_output_markers_for_coding.join(', ')
      : previous.markersText,
  };
}

export function buildQualityGatesPayload(state: QualityGateEditorState): any {
  return {
    quality_gates: {
      enabled: !!state.enabled,
      autopilot_enforce: !!state.autopilotEnforce,
      min_output_chars: Math.max(1, Number(state.minOutputChars || 8)),
      coding_keywords: parseCommaListValue(state.codingKeywordsText),
      required_output_markers_for_coding: parseCommaListValue(state.markersText),
    }
  };
}
