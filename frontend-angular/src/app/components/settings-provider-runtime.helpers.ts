import { findMatchingCatalogModelId, normalizeOpenAICompatibleBaseUrlValue } from './settings-config.helpers';

/**
 * Pure provider/runtime resolution for the LLM settings: base URLs, local vs.
 * cloud classification, provider catalog projections and configuration warnings.
 * All functions read the config; none of them mutate it.
 */

export interface LocalOpenAiBackendDraft {
  provider: string;
  name: string;
  base_url: string;
  api_key_profile: string;
  models_text: string;
  supports_tool_calls: boolean;
}

export interface CatalogProviderEntry { id: string; available: boolean; model_count: number }
export interface CatalogModelEntry { id: string; display_name: string; context_length: number | null }

const DEFAULT_LMSTUDIO_URL = 'http://192.168.56.1:1234/v1';
const DEFAULT_OPENAI_URL = 'https://api.openai.com/v1/chat/completions';
const PROVIDER_DEFAULT_URLS: Record<string, string> = {
  ollama: 'http://localhost:11434/api/generate',
  lmstudio: DEFAULT_LMSTUDIO_URL,
  openai: DEFAULT_OPENAI_URL,
  codex: DEFAULT_OPENAI_URL,
  anthropic: 'https://api.anthropic.com/v1/messages'
};
const LOCAL_URL_MARKERS = ['localhost', '127.0.0.1', 'host.docker.internal', '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.'];

export function parseCommaListValue(text: string): string[] {
  return String(text || '')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
}

export function isProbablyLocalUrlValue(url: string): boolean {
  const raw = String(url || '').trim().toLowerCase();
  if (!raw) return false;
  return LOCAL_URL_MARKERS.some(marker => raw.includes(marker));
}

export function requiresApiKeyValue(provider: string): boolean {
  return provider === 'openai' || provider === 'codex' || provider === 'anthropic';
}

export function hasApiKeyValue(config: any, provider: string): boolean {
  const llmCfg = config?.llm_config || {};
  if (llmCfg?.provider === provider && llmCfg?.api_key) return true;
  if (provider === 'openai' || provider === 'codex') return Boolean(config?.openai_api_key);
  if (provider === 'anthropic') return Boolean(config?.anthropic_api_key);
  return false;
}

export function resolveBaseUrlForProviderValue(config: any, provider: string, localBackends: LocalOpenAiBackendDraft[]): string {
  const normalizedProvider = String(provider || '').trim().toLowerCase();
  const llmCfg = config?.llm_config || {};
  if (llmCfg?.provider === normalizedProvider && llmCfg?.base_url) {
    return normalizeOpenAICompatibleBaseUrlValue(llmCfg.base_url);
  }
  const localBackend = localBackends.find((entry) => entry.provider === normalizedProvider);
  if (localBackend?.base_url) {
    return normalizeOpenAICompatibleBaseUrlValue(localBackend.base_url);
  }
  const key = `${normalizedProvider}_url`;
  return normalizeOpenAICompatibleBaseUrlValue(config?.[key] || PROVIDER_DEFAULT_URLS[normalizedProvider] || '(nicht gesetzt)');
}

export function resolveCodexCliEffectiveBaseUrlValue(config: any, localBackends: LocalOpenAiBackendDraft[]): string {
  const codexCfg = config?.codex_cli || {};
  const targetProvider = String(codexCfg?.target_provider || '').trim().toLowerCase();
  if (targetProvider) {
    const localBackend = localBackends.find((entry) => entry.provider === targetProvider);
    if (targetProvider === 'lmstudio') {
      return normalizeOpenAICompatibleBaseUrlValue(config?.lmstudio_url || DEFAULT_LMSTUDIO_URL);
    }
    if (localBackend?.base_url) return normalizeOpenAICompatibleBaseUrlValue(localBackend.base_url);
  }
  if (codexCfg?.base_url) return normalizeOpenAICompatibleBaseUrlValue(codexCfg.base_url);
  if (codexCfg?.prefer_lmstudio !== false) return normalizeOpenAICompatibleBaseUrlValue(config?.lmstudio_url || DEFAULT_LMSTUDIO_URL);
  return normalizeOpenAICompatibleBaseUrlValue(config?.openai_url || DEFAULT_OPENAI_URL);
}

export function describeCodexCliTargetValue(config: any, codexUrl: string): string {
  const runtime = isProbablyLocalUrlValue(codexUrl) ? 'local openai-compatible' : 'cloud/openai-compatible';
  const targetProvider = String(config?.codex_cli?.target_provider || '').trim().toLowerCase();
  return `${runtime}${targetProvider ? ` via ${targetProvider}` : ''} (${codexUrl})`;
}

export function classifyProviderRuntimeValue(
  provider: string,
  baseUrl: string,
  codexUrl: string,
  localBackends: LocalOpenAiBackendDraft[],
): string {
  const p = String(provider || '').trim().toLowerCase();
  if (p === 'lmstudio' || p === 'ollama' || localBackends.some((entry) => entry.provider === p)) return 'local runtime';
  if (p === 'codex') return isProbablyLocalUrlValue(codexUrl) ? 'local openai-compatible' : 'cloud/openai-compatible';
  if (isProbablyLocalUrlValue(baseUrl)) return 'local openai-compatible';
  return 'cloud provider';
}

export interface LlmWarningInputs {
  config: any;
  provider: string;
  effectiveBaseUrl: string;
  codexUrl: string;
  catalogProviders: CatalogProviderEntry[];
  localBackends: LocalOpenAiBackendDraft[];
}

export function collectLlmConfigurationWarnings(inputs: LlmWarningInputs): string[] {
  const { config, provider, effectiveBaseUrl, codexUrl, catalogProviders, localBackends } = inputs;
  const warnings: string[] = [];
  const providerBlock = catalogProviders.find((entry) => entry.id === provider);
  if (providerBlock && !providerBlock.available) {
    warnings.push(`Provider ${provider} ist laut Katalog aktuell nicht verfuegbar.`);
  }
  if (provider === 'lmstudio') {
    if (!String(config?.lmstudio_url || '').trim()) {
      warnings.push('LM Studio ist Default-Provider, aber die LM-Studio-URL ist nicht gesetzt.');
    } else if (!isProbablyLocalUrlValue(effectiveBaseUrl)) {
      warnings.push('LM Studio ist als lokaler Standard gesetzt, die konfigurierte URL wirkt jedoch nicht lokal.');
    }
  }
  if (provider === 'ollama' && !isProbablyLocalUrlValue(effectiveBaseUrl)) {
    warnings.push('Ollama sollte auf eine lokale Runtime zeigen, die aktuelle URL wirkt jedoch nicht lokal.');
  }
  if (requiresApiKeyValue(provider) && !hasApiKeyValue(config, provider)) {
    warnings.push(`Provider ${provider} benoetigt einen API-Key oder ein passendes Profil.`);
  }

  const codexProfile = String(config?.codex_cli?.api_key_profile || '').trim();
  const codexTargetProvider = String(config?.codex_cli?.target_provider || '').trim().toLowerCase();
  if (codexTargetProvider && codexTargetProvider !== 'lmstudio' && !localBackends.some((entry) => entry.provider === codexTargetProvider)) {
    warnings.push(`Codex CLI target_provider ${codexTargetProvider} ist nicht in local_openai_backends konfiguriert.`);
  }
  if (!codexUrl) {
    warnings.push('Codex CLI hat kein effektives Ziel; setzen Sie codex_cli.base_url oder aktivieren Sie LM Studio als Fallback.');
  }
  if (!isProbablyLocalUrlValue(codexUrl) && !codexProfile && !hasApiKeyValue(config, 'codex')) {
    warnings.push('Codex CLI zeigt auf eine Cloud/OpenAI-kompatible Runtime, aber weder API-Key-Profil noch globaler Key sind erkennbar.');
  }
  return warnings;
}

export function normalizeLocalOpenAiBackendsValue(items: any): any[] {
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => {
      const provider = String(item?.provider || item?.id || '').trim().toLowerCase();
      const models = parseCommaListValue(item?.models_text ?? item?.models);
      if (!provider) return null;
      return {
        id: provider,
        provider,
        name: String(item?.name || provider).trim(),
        base_url: normalizeOpenAICompatibleBaseUrlValue(item?.base_url),
        api_key_profile: String(item?.api_key_profile || '').trim(),
        models,
        models_text: models.join(', '),
        supports_tool_calls: item?.supports_tool_calls !== false,
      };
    })
    .filter((item): item is any => !!item);
}

export function createEmptyLocalOpenAiBackend(): LocalOpenAiBackendDraft {
  return {
    provider: '',
    name: '',
    base_url: '',
    api_key_profile: '',
    models_text: '',
    supports_tool_calls: true,
  };
}

export function listCatalogProvidersValue(providerCatalog: any, localBackends: LocalOpenAiBackendDraft[]): CatalogProviderEntry[] {
  const providers = Array.isArray(providerCatalog?.providers) ? providerCatalog.providers : [];
  if (!providers.length) {
    return [
      { id: 'ollama', available: true, model_count: 0 },
      { id: 'lmstudio', available: true, model_count: 0 },
      { id: 'openai', available: true, model_count: 0 },
      { id: 'codex', available: true, model_count: 0 },
      { id: 'anthropic', available: true, model_count: 0 },
      ...localBackends.map((backend) => ({
        id: backend.provider,
        available: Boolean(backend.base_url),
        model_count: parseCommaListValue(backend.models_text).length,
      })),
    ];
  }
  return providers
    .map((p: any) => ({
      id: String(p?.provider || ''),
      available: !!p?.available,
      model_count: Number(p?.model_count || 0),
    }))
    .filter((p: CatalogProviderEntry) => !!p.id);
}

export function groupProvidersForSelectValue(
  providers: CatalogProviderEntry[],
  localBackends: LocalOpenAiBackendDraft[],
): Array<{ label: string; providers: CatalogProviderEntry[] }> {
  const localIds = new Set(['lmstudio', 'ollama', ...localBackends.map((entry) => entry.provider)]);
  const cloudIds = new Set(['openai', 'codex', 'anthropic']);
  return [
    {
      label: 'Lokale Runtimes',
      providers: providers.filter((provider) => localIds.has(provider.id)),
    },
    {
      label: 'Cloud / Hosted Provider',
      providers: providers.filter((provider) => cloudIds.has(provider.id)),
    },
  ].filter((group) => group.providers.length > 0);
}

export function listCatalogModelsValue(providerCatalog: any, providerId: string): CatalogModelEntry[] {
  const providers = Array.isArray(providerCatalog?.providers) ? providerCatalog.providers : [];
  const block = providers.find((p: any) => String(p?.provider || '') === String(providerId || ''));
  const models = Array.isArray(block?.models) ? block.models : [];
  if (!models.length) {
    return [];
  }
  return models
    .map((m: any) => ({
      id: String(m?.id || ''),
      display_name: String(m?.display_name || m?.id || ''),
      context_length: m?.context_length ?? null,
    }))
    .filter((m: CatalogModelEntry) => !!m.id);
}

/**
 * Returns the catalog model id the current selection should snap to, or null
 * when the selection must stay unchanged (empty catalog / already consistent).
 */
export function resolveConsistentCatalogModelId(currentModel: any, models: CatalogModelEntry[]): string | null {
  if (!models.length) return null;
  const current = String(currentModel || '').trim();
  const matched = findMatchingCatalogModelId(current, models);
  if (matched) return matched;
  if (!current || !models.some(m => m.id === current)) return models[0].id;
  return null;
}

export function isModelInCatalogValue(currentModel: any, models: CatalogModelEntry[]): boolean {
  const current = String(currentModel || '').trim();
  if (!current || !models.length) return false;
  return !!findMatchingCatalogModelId(current, models);
}
