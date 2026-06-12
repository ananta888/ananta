export function normalizeOpenAICompatibleBaseUrlValue(url: any): string {
  const raw = String(url || '').trim();
  if (!raw) return '';
  let normalized = raw;
  for (const suffix of ['/chat/completions', '/completions', '/responses']) {
    if (normalized.endsWith(suffix)) {
      normalized = normalized.slice(0, -suffix.length);
      break;
    }
  }
  return normalized.replace(/\/+$/, '');
}

export function normalizeHubCopilotConfigValue(value: any): any {
  const raw = value && typeof value === 'object' ? value : {};
  const strategyMode = String(raw.strategy_mode || 'planning_only').trim().toLowerCase() === 'planning_and_routing'
    ? 'planning_and_routing'
    : 'planning_only';
  const temp = Number(raw.temperature);
  return {
    enabled: raw.enabled === true,
    provider: String(raw.provider || '').trim().toLowerCase(),
    model: String(raw.model || '').trim(),
    base_url: normalizeOpenAICompatibleBaseUrlValue(raw.base_url),
    temperature: Number.isFinite(temp) ? Math.max(0, Math.min(2, temp)) : 0.2,
    strategy_mode: strategyMode,
  };
}

export function resolveHubCopilotProviderValue(config: any, effectiveProvider: string): string {
  const provider = String(config?.hub_copilot?.provider || '').trim().toLowerCase();
  if (provider) return provider;
  const llmProvider = String(config?.llm_config?.provider || '').trim().toLowerCase();
  return llmProvider || String(effectiveProvider || '').trim().toLowerCase();
}

export function resolveHubCopilotModelValue(config: any, effectiveModel: string): string {
  const model = String(config?.hub_copilot?.model || '').trim();
  if (model) return model;
  const llmModel = String(config?.llm_config?.model || '').trim();
  return llmModel || String(effectiveModel || '').trim();
}

export function resolveHubCopilotProviderSourceValue(config: any): string {
  if (String(config?.hub_copilot?.provider || '').trim()) return 'hub_copilot.provider';
  if (String(config?.llm_config?.provider || '').trim()) return 'llm_config.provider';
  return 'default_provider';
}

export function resolveHubCopilotModelSourceValue(config: any): string {
  if (String(config?.hub_copilot?.model || '').trim()) return 'hub_copilot.model';
  if (String(config?.llm_config?.model || '').trim()) return 'llm_config.model';
  return 'default_model';
}

export function normalizeContextBundlePolicyConfigValue(value: any): any {
  const raw = value && typeof value === 'object' ? value : {};
  const mode = ['compact', 'standard', 'full'].includes(String(raw.mode || '').trim().toLowerCase())
    ? String(raw.mode || '').trim().toLowerCase()
    : 'full';
  const windowProfile = ['compact_12k', 'standard_32k', 'full_64k'].includes(String(raw.window_profile || '').trim().toLowerCase())
    ? String(raw.window_profile || '').trim().toLowerCase()
    : 'standard_32k';
  const compactMaxChunks = Number(raw.compact_max_chunks);
  const standardMaxChunks = Number(raw.standard_max_chunks);
  const compactBudgetTokens = Number(raw.compact_budget_tokens);
  const standardBudgetTokens = Number(raw.standard_budget_tokens);
  const fullBudgetTokens = Number(raw.full_budget_tokens);
  return {
    mode,
    window_profile: windowProfile,
    compact_max_chunks: Number.isFinite(compactMaxChunks) ? Math.max(1, Math.min(50, compactMaxChunks)) : 3,
    standard_max_chunks: Number.isFinite(standardMaxChunks) ? Math.max(1, Math.min(50, standardMaxChunks)) : 8,
    compact_budget_tokens: Number.isFinite(compactBudgetTokens) ? Math.max(4096, Math.min(131072, compactBudgetTokens)) : 12000,
    standard_budget_tokens: Number.isFinite(standardBudgetTokens) ? Math.max(4096, Math.min(131072, standardBudgetTokens)) : 32000,
    full_budget_tokens: Number.isFinite(fullBudgetTokens) ? Math.max(4096, Math.min(131072, fullBudgetTokens)) : 64000,
    budget_tokens_by_mode: {
      compact: Number.isFinite(compactBudgetTokens) ? Math.max(4096, Math.min(131072, compactBudgetTokens)) : 12000,
      standard: Number.isFinite(standardBudgetTokens) ? Math.max(4096, Math.min(131072, standardBudgetTokens)) : 32000,
      full: Number.isFinite(fullBudgetTokens) ? Math.max(4096, Math.min(131072, fullBudgetTokens)) : 64000,
    },
  };
}

export function normalizeArtifactFlowConfigValue(value: any): any {
  const raw = value && typeof value === 'object' ? value : {};
  const ragTopK = Number(raw.rag_top_k);
  const maxTasks = Number(raw.max_tasks);
  const maxWorkerJobsPerTask = Number(raw.max_worker_jobs_per_task);
  return {
    enabled: raw.enabled !== false,
    rag_enabled: raw.rag_enabled !== false,
    rag_top_k: Number.isFinite(ragTopK) ? Math.max(1, Math.min(20, ragTopK)) : 3,
    rag_include_content: raw.rag_include_content === true,
    max_tasks: Number.isFinite(maxTasks) ? Math.max(1, Math.min(200, maxTasks)) : 30,
    max_worker_jobs_per_task: Number.isFinite(maxWorkerJobsPerTask)
      ? Math.max(1, Math.min(20, maxWorkerJobsPerTask))
      : 5,
  };
}

export function normalizeOpencodeRuntimeConfigValue(value: any): any {
  const raw = value && typeof value === 'object' ? value : {};
  const toolMode = String(raw.tool_mode || 'full').trim().toLowerCase();
  const executionMode = String(raw.execution_mode || 'live_terminal').trim().toLowerCase();
  const interactiveLaunchMode = String(raw.interactive_launch_mode || 'run').trim().toLowerCase();
  return {
    tool_mode: ['full', 'readonly', 'toolless'].includes(toolMode) ? toolMode : 'full',
    execution_mode: ['backend', 'live_terminal', 'interactive_terminal'].includes(executionMode) ? executionMode : 'live_terminal',
    interactive_launch_mode: ['run', 'tui'].includes(interactiveLaunchMode) ? interactiveLaunchMode : 'run',
  };
}

export function normalizeWorkerRuntimeConfigValue(value: any): any {
  const raw = value && typeof value === 'object' ? value : {};
  const workspaceRoot = String(raw.workspace_root || '').trim();
  const workspaceReuseMode = String(raw.workspace_reuse_mode || 'goal_worker').trim().toLowerCase();
  return {
    workspace_root: workspaceRoot || null,
    workspace_reuse_mode: ['task', 'goal_worker'].includes(workspaceReuseMode) ? workspaceReuseMode : 'goal_worker',
  };
}

export function normalizeModelOverrideMapValue(value: any): Record<string, string> {
  const raw = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const normalized: Record<string, string> = {};
  for (const [key, model] of Object.entries(raw)) {
    const normalizedKey = String(key || '').trim().toLowerCase();
    const normalizedModel = String(model || '').trim();
    if (!normalizedKey || !normalizedModel) continue;
    normalized[normalizedKey] = normalizedModel;
  }
  return normalized;
}

export type OllamaStrategyRow = {
  id: string;
  category: string;
  recommended_use: string;
  opencode_worker: string;
  note: string;
};

export type ProjectModelRoutingRecommendation = {
  default_provider: string;
  default_model: string;
  hub_copilot_provider: string;
  hub_copilot_model: string;
  task_kind_model_overrides: Record<string, string>;
  role_model_overrides: Record<string, string>;
  template_model_overrides: Record<string, string>;
  warnings: string[];
};

export function normalizeResearchBackendConfigValue(value: any): any {
  const raw = value && typeof value === 'object' ? value : {};
  const provider = ['deerflow', 'ananta_research'].includes(String(raw.provider || '').trim().toLowerCase())
    ? String(raw.provider || '').trim().toLowerCase()
    : 'deerflow';
  const mode = String(raw.mode || 'cli').trim().toLowerCase() || 'cli';
  const resultFormat = String(raw.result_format || 'markdown').trim().toLowerCase() || 'markdown';
  const timeoutSeconds = Number(raw.timeout_seconds);
  return {
    ...raw,
    provider,
    enabled: raw.enabled === true,
    mode,
    command: String(raw.command || '').trim(),
    working_dir: String(raw.working_dir || '').trim(),
    timeout_seconds: Number.isFinite(timeoutSeconds) ? Math.max(30, Math.min(7200, timeoutSeconds)) : 900,
    result_format: resultFormat,
  };
}

export function resolveContextBundlePolicyValue(config: any): any {
  const normalized = normalizeContextBundlePolicyConfigValue(config?.context_bundle_policy);
  const budgetByMode = normalized.budget_tokens_by_mode || {};
  const modeProfile = normalized.mode === 'compact'
    ? { bundle_strategy: 'minimal', explainability_level: 'minimal', chunk_text_style: 'compressed_snippets' }
    : normalized.mode === 'standard'
      ? { bundle_strategy: 'balanced', explainability_level: 'balanced', chunk_text_style: 'balanced_snippets' }
      : { bundle_strategy: 'deep', explainability_level: 'detailed', chunk_text_style: 'detailed_context' };
  if (normalized.mode === 'compact') {
    return {
      ...normalized,
      include_context_text: false,
      max_chunks: normalized.compact_max_chunks,
      total_budget_tokens: budgetByMode.compact || normalized.compact_budget_tokens || 12000,
      ...modeProfile,
    };
  }
  if (normalized.mode === 'standard') {
    return {
      ...normalized,
      include_context_text: true,
      max_chunks: normalized.standard_max_chunks,
      total_budget_tokens: budgetByMode.standard || normalized.standard_budget_tokens || 32000,
      ...modeProfile,
    };
  }
  return {
    ...normalized,
    include_context_text: true,
    max_chunks: null,
    total_budget_tokens: budgetByMode.full || normalized.full_budget_tokens || 64000,
    ...modeProfile,
  };
}

function findFirstModelByTokens(modelIds: string[], tokenSets: string[][]): string {
  const normalizedIds = modelIds
    .map((id) => String(id || '').trim())
    .filter(Boolean);
  for (const tokenSet of tokenSets) {
    const match = normalizedIds.find((id) => {
      const lower = id.toLowerCase();
      return tokenSet.every((token) => lower.includes(token));
    });
    if (match) return match;
  }
  return '';
}

function modelIdentifierTokens(modelId: string): string[] {
  return String(modelId || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9.]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean);
}

function modelIdentifierMatches(left: string, right: string): boolean {
  const leftValue = String(left || '').trim();
  const rightValue = String(right || '').trim();
  if (!leftValue || !rightValue) return false;
  if (leftValue.toLowerCase() === rightValue.toLowerCase()) return true;
  const leftTokens = new Set(modelIdentifierTokens(leftValue));
  const rightTokens = new Set(modelIdentifierTokens(rightValue));
  const overlap = [...leftTokens].filter((token) => rightTokens.has(token));
  if (overlap.length < 2) return false;
  const leftSubset = [...leftTokens].every((token) => rightTokens.has(token));
  const rightSubset = [...rightTokens].every((token) => leftTokens.has(token));
  return leftSubset || rightSubset;
}

export function findMatchingCatalogModelId(current: string, models: Array<{ id: string }>): string {
  const normalizedCurrent = String(current || '').trim();
  if (!normalizedCurrent) return '';
  const exact = models.find((m) => String(m.id || '').trim() === normalizedCurrent);
  if (exact) return exact.id;
  const fuzzy = models.find((m) => modelIdentifierMatches(normalizedCurrent, String(m.id || '')));
  return fuzzy?.id || '';
}

function classifyOllamaModel(modelId: string): Omit<OllamaStrategyRow, 'id'> {
  const lower = String(modelId || '').trim().toLowerCase();
  if (!lower) {
    return {
      category: 'general',
      recommended_use: 'Fallback / manuelle Zuordnung',
      opencode_worker: 'bedingt',
      note: 'Keine Heuristik verfuegbar.',
    };
  }
  if (lower.includes('ananta-default')) {
    return {
      category: 'general',
      recommended_use: 'Nicht fuer produktive OpenCode-Worker verwenden',
      opencode_worker: 'nein',
      note: 'Live-Click zeigte fehlende Tool-Unterstuetzung im Worker-Pfad.',
    };
  }
  if (lower.includes('mmproj') || lower.includes('voxtral')) {
    return {
      category: 'multimodal',
      recommended_use: 'Multimodale Experimente, Audio/Bild-nahe Spezialfaelle',
      opencode_worker: 'nein',
      note: 'Nicht die beste Standardwahl fuer Shell-/Code-Delegation.',
    };
  }
  if (lower.includes('coder')) {
    if (lower.includes('14b') || lower.includes('20b') || lower.includes('7b') || lower.includes('deepseek-coder-v2-lite')) {
      return {
        category: 'coding',
        recommended_use: 'OpenCode-Worker fuer Implementierung, Refactoring und Tool-Aufrufe',
        opencode_worker: 'ja',
        note: 'Code-orientierte Modelle fuer Shell-, Edit- und Multi-File-Tasks.',
      };
    }
    return {
      category: 'coding',
      recommended_use: 'Schnelle Coding-Hilfe, Vorentwuerfe und leichte Worker-Aufgaben',
      opencode_worker: 'bedingt',
      note: 'Gut fuer guenstige Drafts, aber schwacher fuer lange Tool-Loops.',
    };
  }
  if (lower.includes('reasoning') || lower.includes('thinking') || lower.includes('glm-z1')) {
    return {
      category: 'reasoning',
      recommended_use: 'Hub-Planung, Review, Analyse, Triage',
      opencode_worker: 'bedingt',
      note: 'Eher fuer Planen/Bewerten als fuer direkte Code-Ausfuehrung.',
    };
  }
  if (lower.includes('lfm2.5')) {
    return {
      category: 'planning',
      recommended_use: 'Leichter Hub-Planer, Routing, Backlog-Triage',
      opencode_worker: 'bedingt',
      note: 'Projektweit oft genutzt, aber bisher schwache strikte Benchmark-Signale.',
    };
  }
  if (lower.includes('phi-4') || lower.includes('glm-4-9b')) {
    return {
      category: 'review',
      recommended_use: 'Review, Architekturabgleich, Fehleranalyse',
      opencode_worker: 'bedingt',
      note: 'Solider Zweitpfad fuer Bewertung und technische Rueckkopplung.',
    };
  }
  if (lower.includes('ministral') || lower.includes('mistral') || lower.includes('llama') || lower.includes('qwen2.5-0.5b-instruct') || lower.includes('openai-7b')) {
    return {
      category: 'general',
      recommended_use: 'Dokumentation, Template-Texte, allgemeine Assists',
      opencode_worker: 'bedingt',
      note: 'Generalisten fuer textlastige oder leichte Assistenz-Aufgaben.',
    };
  }
  if (lower.includes('gemma')) {
    return {
      category: 'general',
      recommended_use: 'Template-/Prompt-Ausarbeitung und leichte Wissensaufgaben',
      opencode_worker: 'bedingt',
      note: 'Eher fuer Struktur/Text als fuer komplexe Tool-Loops.',
    };
  }
  if (lower.includes('gpt-oss-20b')) {
    return {
      category: 'general',
      recommended_use: 'Schwere Generalisten- oder Coding-Fallbacks',
      opencode_worker: 'ja',
      note: 'Leistungsstark, aber ressourcenintensiver als kleinere Standardpfade.',
    };
  }
  return {
    category: 'general',
    recommended_use: 'Manuelle Einordnung je Task',
    opencode_worker: 'bedingt',
    note: 'Noch keine projektspezifische Sonderregel hinterlegt.',
  };
}

export function buildOllamaModelStrategyRowsValue(modelIds: string[]): OllamaStrategyRow[] {
  return modelIds
    .map((id) => String(id || '').trim())
    .filter(Boolean)
    .map((id) => ({
      id,
      ...classifyOllamaModel(id),
    }));
}

export function buildProjectModelRoutingRecommendationValue(modelIds: string[]): ProjectModelRoutingRecommendation {
  const normalizedIds = modelIds
    .map((id) => String(id || '').trim())
    .filter(Boolean);
  const plannerModel = findFirstModelByTokens(normalizedIds, [
    ['lfm2.5'],
    ['phi-4', 'reasoning'],
    ['glm-z1'],
    ['qwen2.5-0.5b-instruct'],
  ]);
  const codingPrimary = findFirstModelByTokens(normalizedIds, [
    ['qwen2.5', 'coder', '14b'],
    ['gpt-oss-20b', 'coder'],
    ['qwen2.5', 'coder', '7b'],
    ['deepseek-coder-v2-lite'],
    ['qwen2.5', 'coder', '3b'],
    ['qwen2.5', 'coder', '0.5b'],
  ]);
  const reviewModel = findFirstModelByTokens(normalizedIds, [
    ['glm-4-9b'],
    ['phi-4', 'reasoning'],
    ['meta-llama', '8b'],
    ['mistral-7b'],
  ]);
  const docsModel = findFirstModelByTokens(normalizedIds, [
    ['ministral', '3b'],
    ['qwen2.5-0.5b-instruct'],
    ['gemma-4', 'e2b-it'],
    ['mistral-7b'],
  ]);
  const fallbackModel = codingPrimary || reviewModel || plannerModel || docsModel || normalizedIds[0] || '';
  const warnings: string[] = [];
  if (normalizedIds.some((id) => id.toLowerCase().includes('ananta-default'))) {
    warnings.push('ananta-default ist vorhanden, sollte aber fuer OpenCode-Worker nicht als Default genutzt werden.');
  }
  if (!codingPrimary) {
    warnings.push('Kein klarer Coder-Favorit erkannt; Coding-Tasks fallen auf den allgemeinen Fallback zurueck.');
  }
  return {
    default_provider: 'ollama',
    default_model: plannerModel || fallbackModel,
    hub_copilot_provider: 'ollama',
    hub_copilot_model: plannerModel || reviewModel || fallbackModel,
    task_kind_model_overrides: normalizeModelOverrideMapValue({
      planning: plannerModel || fallbackModel,
      analysis: reviewModel || plannerModel || fallbackModel,
      research: reviewModel || plannerModel || fallbackModel,
      coding: codingPrimary || fallbackModel,
      testing: codingPrimary || reviewModel || fallbackModel,
      review: reviewModel || plannerModel || fallbackModel,
      documentation: docsModel || plannerModel || fallbackModel,
    }),
    role_model_overrides: normalizeModelOverrideMapValue({
      architect: reviewModel || plannerModel || fallbackModel,
      'product owner': plannerModel || reviewModel || fallbackModel,
      'scrum master': plannerModel || reviewModel || fallbackModel,
      'backend developer': codingPrimary || fallbackModel,
      'frontend developer': codingPrimary || fallbackModel,
      'fullstack developer': codingPrimary || fallbackModel,
      'devops engineer': codingPrimary || reviewModel || fallbackModel,
      'qa/test engineer': reviewModel || codingPrimary || fallbackModel,
      reviewer: reviewModel || plannerModel || fallbackModel,
      'fullstack reviewer': reviewModel || plannerModel || fallbackModel,
    }),
    template_model_overrides: normalizeModelOverrideMapValue({
      'backend implementation': codingPrimary || fallbackModel,
      'frontend implementation': codingPrimary || fallbackModel,
      'code review': reviewModel || plannerModel || fallbackModel,
      documentation: docsModel || plannerModel || fallbackModel,
    }),
    warnings,
  };
}

export function createDefaultSettingsConfig(): any {
  return {
    runtime_profile: 'local-dev',
    governance_mode: 'balanced',
    hub_copilot: normalizeHubCopilotConfigValue(undefined),
    context_bundle_policy: normalizeContextBundlePolicyConfigValue(undefined),
    artifact_flow: normalizeArtifactFlowConfigValue(undefined),
    opencode_runtime: normalizeOpencodeRuntimeConfigValue(undefined),
    worker_runtime: normalizeWorkerRuntimeConfigValue(undefined),
    role_model_overrides: {},
    template_model_overrides: {},
    task_kind_model_overrides: {},
    research_backend: normalizeResearchBackendConfigValue(undefined),
    codex_cli: { target_provider: '', base_url: '', api_key_profile: '', prefer_lmstudio: true },
    cli_session_mode: { enabled: false, stateful_backends: [] },
    local_openai_backends: [],
  };
}

