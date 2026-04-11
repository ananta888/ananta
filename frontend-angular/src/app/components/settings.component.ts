import { Component, OnInit, inject } from '@angular/core';

import { JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { SystemFacade } from '../features/system/system.facade';
import { ChangePasswordComponent } from './change-password.component';
import { UserManagementComponent } from './user-management.component';
import { MfaSetupComponent } from './mfa-setup.component';
import { TooltipDirective } from '../directives/tooltip.directive';

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

type OllamaStrategyRow = {
  id: string;
  category: string;
  recommended_use: string;
  opencode_worker: string;
  note: string;
};

type ProjectModelRoutingRecommendation = {
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

function findMatchingCatalogModelId(current: string, models: Array<{ id: string }>): string {
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

function createDefaultSettingsConfig(): any {
  return {
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

@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [FormsModule, JsonPipe, ChangePasswordComponent, UserManagementComponent, MfaSetupComponent, TooltipDirective],
  template: `
    <div class="row flex-between">
      <h2>System-Einstellungen</h2>
      <div class="row gap-sm">
        <button (click)="toggleDarkMode()" class="button-outline">
          {{ isDarkMode ? 'Heller Modus' : 'Dunkler Modus' }}
        </button>
        <button (click)="load()" class="button-outline">Aktualisieren</button>
      </div>
    </div>
    <p class="muted">Konfiguration des Hub-Agenten und globale Parameter.</p>

    <div class="row gap-sm flex-wrap mb-md">
      <button class="button-outline" [class.active-toggle]="selectedSection==='account'" (click)="setSection('account')">Account</button>
      <button class="button-outline" [class.active-toggle]="selectedSection==='llm'" (click)="setSection('llm')">LLM und KI</button>
      <button class="button-outline" [class.active-toggle]="selectedSection==='quality'" (click)="setSection('quality')">Qualitaetsregeln</button>
      <button class="button-outline" [class.active-toggle]="selectedSection==='system'" (click)="setSection('system')">System</button>
    </div>
    
    @if (selectedSection === 'account') {
      <div class="grid cols-2">
        <app-change-password class="block mb-lg"></app-change-password>
        <app-mfa-setup class="block mb-lg"></app-mfa-setup>
      </div>

      @if (isAdmin) {
        <app-user-management class="block mb-lg"></app-user-management>
      }
    }
    
    @if (!hub) {
      <div class="card danger">
        <p>Kein Hub-Agent konfiguriert. Bitte legen Sie einen Agenten mit der Rolle "hub" fest.</p>
      </div>
    }
    
    @if (hub) {
      <div class="grid">
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Runtime Routing</h3>
          <p class="muted">Lokale Modell-Runtimes, Cloud-Provider und CLI-Backends werden getrennt angezeigt, damit der effektive Laufzeitpfad nachvollziehbar bleibt.</p>
          <div class="grid cols-3">
            <div>
              <div class="muted">Lokale Runtimes</div>
              <div class="font-sm">{{ getRuntimeGroupSummary('local') }}</div>
            </div>
            <div>
              <div class="muted">Cloud / Hosted</div>
              <div class="font-sm">{{ getRuntimeGroupSummary('cloud') }}</div>
            </div>
            <div>
              <div class="muted">CLI-Backends</div>
              <div class="font-sm">{{ getRuntimeGroupSummary('cli') }}</div>
            </div>
          </div>
          <div class="grid cols-2 mt-lg">
            <div>
              <div class="muted">Aktiver Default-Pfad</div>
              <div>{{ getEffectiveProvider() }} -> {{ getProviderRuntimeKind(getEffectiveProvider()) }}</div>
            </div>
            <div>
              <div class="muted">Provider-Ziel</div>
              <div>{{ getProviderEndpointSummary(getEffectiveProvider()) }}</div>
            </div>
          </div>
          @if (getLlmConfigurationWarnings().length) {
            <div class="danger font-sm mt-md">
              @for (warning of getLlmConfigurationWarnings(); track warning) {
                <div>{{ warning }}</div>
              }
            </div>
          } @else {
            <div class="muted font-sm mt-md">Keine offensichtlichen Runtime-Konflikte erkannt.</div>
          }
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card card-info">
          <h3>Hinweis LLM-Konfiguration</h3>
          <p class="muted mt-sm">Diese Werte werden standardmaessig fuer KI-Funktionen verwendet.</p>
          <div class="grid cols-2">
            <div>
              <div class="muted">Provider</div>
              <div>{{ getEffectiveProvider() }}</div>
            </div>
            <div>
              <div class="muted">Model</div>
              <div>{{ getEffectiveModel() }}</div>
            </div>
            <div>
              <div class="muted">Base URL</div>
              <div>{{ getEffectiveBaseUrl() }}</div>
            </div>
            <div>
              <div class="muted">Runtime</div>
              <div>{{ getProviderRuntimeKind(getEffectiveProvider()) }}</div>
            </div>
            <div>
              <div class="muted">Execution Backend</div>
              <div>{{ (config?.sgpt_execution_backend || 'sgpt') }}</div>
            </div>
            <div>
              <div class="muted">Codex Target</div>
              <div>{{ (config?.codex_cli?.target_provider || 'default') }} / {{ (config?.codex_cli?.base_url || getEffectiveBaseUrl()) }}</div>
            </div>
            <div>
              <div class="muted">API Key</div>
              <div>{{ requiresApiKey(getEffectiveProvider()) ? (hasApiKey(getEffectiveProvider()) ? 'ok' : 'missing') : 'not required' }}</div>
            </div>
            <div>
              <div class="muted">CLI Session Mode</div>
              <div>{{ config?.cli_session_mode?.enabled ? 'enabled' : 'disabled' }}</div>
            </div>
            <div>
              <div class="muted">Stateful Backends</div>
              <div>{{ (config?.cli_session_mode?.stateful_backends || []).join(', ') || 'n/a' }}</div>
            </div>
            <div>
              <div class="muted">OpenCode Tool-Modus</div>
              <div>{{ config?.opencode_runtime?.tool_mode || 'full' }}</div>
            </div>
            <div>
              <div class="muted">OpenCode Ausfuehrung</div>
              <div>{{ config?.opencode_runtime?.execution_mode || 'live_terminal' }}</div>
            </div>
            <div>
              <div class="muted">Worker Workspace Root</div>
              <div>{{ config?.worker_runtime?.workspace_root || '(default)' }}</div>
            </div>
            <div>
              <div class="muted">Workspace Reuse</div>
              <div>{{ config?.worker_runtime?.workspace_reuse_mode || 'goal_worker' }}</div>
            </div>
          </div>
        </div>
        <div class="card">
          <h3>KI-Unterstützung</h3>
          <p class="muted">Wählen Sie aus, welche Agenten für die KI-Unterstützung im Frontend verwendet werden sollen.</p>
          <div class="grid cols-2">
            <label>Agent für Templates
              <select [(ngModel)]="config.template_agent_name">
                <option [ngValue]="undefined">Hub (Standard)</option>
                @for (a of allAgents; track a) {
                  <option [value]="a.name">{{a.name}} ({{a.role}})</option>
                }
              </select>
            </label>
            <label>Agent für Team-Beratung
              <select [(ngModel)]="config.team_agent_name">
                <option [ngValue]="undefined">Hub (Standard)</option>
                @for (a of allAgents; track a) {
                  <option [value]="a.name">{{a.name}} ({{a.role}})</option>
                }
              </select>
            </label>
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card card-info">
          <h3>Strategischer Hub-Copilot</h3>
          <p class="muted">Optionaler Copilot fuer Planung, Routing und Governance im Hub. Er ist nicht fuer die eigentliche Arbeitsausfuehrung gedacht; diese bleibt bei den Workern.</p>
          <label class="row gap-sm mt-md">
            <input type="checkbox" [(ngModel)]="config.hub_copilot.enabled" />
            Strategischen Copilot im Hub aktivieren
          </label>
          <div class="grid cols-2 mt-lg">
            <label>Strategie-Modus
              <select [(ngModel)]="config.hub_copilot.strategy_mode">
                <option value="planning_only">planning_only</option>
                <option value="planning_and_routing">planning_and_routing</option>
              </select>
            </label>
            <label>Provider Override
              <select [(ngModel)]="config.hub_copilot.provider" (ngModelChange)="ensureHubCopilotModelConsistency()">
                <option value="">Default / Fallback</option>
                @for (group of getProviderSelectGroups(); track group.label) {
                  <optgroup [label]="group.label">
                    @for (p of group.providers; track p.id) {
                      <option [value]="p.id">
                        {{ p.id }}{{ p.available ? '' : ' (offline)' }}{{ p.model_count ? ' [' + p.model_count + ']' : '' }}
                      </option>
                    }
                  </optgroup>
                }
              </select>
            </label>
            <label>Model Override
              <select [(ngModel)]="config.hub_copilot.model">
                <option value="">Default / Fallback</option>
                @for (m of getCatalogModels(getHubCopilotProvider()); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}{{ m.context_length ? ' (ctx ' + m.context_length + ')' : '' }}</option>
                }
                @if ((config?.hub_copilot?.model || '').trim() && !isHubCopilotCurrentModelInCatalog()) {
                  <option [value]="config.hub_copilot.model">{{ config.hub_copilot.model }} (custom)</option>
                }
              </select>
            </label>
            <label>Base URL Override
              <input [(ngModel)]="config.hub_copilot.base_url" placeholder="optional, z.B. http://127.0.0.1:1234/v1" />
            </label>
            <label>Temperature
              <input type="number" step="0.1" min="0" max="2" [(ngModel)]="config.hub_copilot.temperature" />
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <div>
              <div class="muted">Effektiver Provider</div>
              <div>{{ getHubCopilotProvider() }} <span class="muted font-sm">({{ getHubCopilotProviderSource() }})</span></div>
            </div>
            <div>
              <div class="muted">Effektives Model</div>
              <div>{{ getHubCopilotModel() }} <span class="muted font-sm">({{ getHubCopilotModelSource() }})</span></div>
            </div>
            <div>
              <div class="muted">Effektive Base URL</div>
              <div>{{ getHubCopilotBaseUrl() || '(default)' }}</div>
            </div>
            <div>
              <div class="muted">Aktiver Status</div>
              <div>{{ isHubCopilotActive() ? 'aktiv' : 'deaktiviert / unvollstaendig konfiguriert' }}</div>
            </div>
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        <div class="card card-info mt-lg">
          <h3>Delegations-Context-Policy</h3>
          <p class="muted">Steuert zentral, wie viel Retrieval-Kontext neue Worker-Delegationen erhalten. Die Policy wirkt additiv auf den bestehenden Bundle-Pfad und erzeugt keinen zweiten Kontext-Workflow.</p>
          <div class="grid cols-2 mt-lg">
            <label>Policy-Modus
              <select [(ngModel)]="config.context_bundle_policy.mode">
                <option value="compact">compact</option>
                <option value="standard">standard</option>
                <option value="full">full</option>
              </select>
            </label>
            <label>Window-Profil
              <select [(ngModel)]="config.context_bundle_policy.window_profile">
                <option value="compact_12k">compact_12k</option>
                <option value="standard_32k">standard_32k</option>
                <option value="full_64k">full_64k</option>
              </select>
            </label>
            <label>Compact max. Chunks
              <input type="number" min="1" max="50" [(ngModel)]="config.context_bundle_policy.compact_max_chunks" />
            </label>
            <label>Standard max. Chunks
              <input type="number" min="1" max="50" [(ngModel)]="config.context_bundle_policy.standard_max_chunks" />
            </label>
            <label>Compact Budget Tokens
              <input type="number" min="4096" max="131072" step="256" [(ngModel)]="config.context_bundle_policy.compact_budget_tokens" />
            </label>
            <label>Standard Budget Tokens
              <input type="number" min="4096" max="131072" step="256" [(ngModel)]="config.context_bundle_policy.standard_budget_tokens" />
            </label>
            <label>Full Budget Tokens
              <input type="number" min="4096" max="131072" step="256" [(ngModel)]="config.context_bundle_policy.full_budget_tokens" />
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <div>
              <div class="muted">Effektiver Kontext-Text</div>
              <div>{{ getEffectiveContextBundlePolicy().include_context_text ? 'enthalten' : 'ausgeblendet' }}</div>
            </div>
            <div>
              <div class="muted">Effektive Chunk-Grenze</div>
              <div>{{ getEffectiveContextBundlePolicy().max_chunks ?? 'unbegrenzt / Vollkontext' }}</div>
            </div>
            <div>
              <div class="muted">Effektives Budget</div>
              <div>{{ getEffectiveContextBundlePolicy().total_budget_tokens }} Tokens</div>
            </div>
            <div>
              <div class="muted">Effektive Strategie</div>
              <div>{{ getEffectiveContextBundlePolicy().bundle_strategy }} / {{ getEffectiveContextBundlePolicy().explainability_level }}</div>
            </div>
            <div>
              <div class="muted">Chunk-Text-Stil</div>
              <div>{{ getEffectiveContextBundlePolicy().chunk_text_style }}</div>
            </div>
            <div>
              <div class="muted">Window-Profil</div>
              <div>{{ getEffectiveContextBundlePolicy().window_profile }}</div>
            </div>
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        <div class="card card-info mt-lg">
          <h3>Artifact Flow & RAG</h3>
          <p class="muted">Konfiguriert Nachvollziehbarkeit und optionale RAG-Anreicherung fuer den Artefakt-Fluss im Orchestrierungs-Read-Model.</p>
          <label class="row gap-sm mt-md">
            <input type="checkbox" [(ngModel)]="config.artifact_flow.enabled" />
            Artifact-Flow Read-Model aktivieren
          </label>
          <label class="row gap-sm mt-sm">
            <input type="checkbox" [(ngModel)]="config.artifact_flow.rag_enabled" [disabled]="!config.artifact_flow.enabled" />
            Direkte RAG-Anreicherung aktivieren
          </label>
          <label class="row gap-sm mt-sm">
            <input type="checkbox" [(ngModel)]="config.artifact_flow.rag_include_content" [disabled]="!config.artifact_flow.enabled || !config.artifact_flow.rag_enabled" />
            RAG-Content im Read-Model anzeigen
          </label>
          <div class="grid cols-2 mt-lg">
            <label>RAG Top-K
              <input type="number" min="1" max="20" [(ngModel)]="config.artifact_flow.rag_top_k" [disabled]="!config.artifact_flow.enabled || !config.artifact_flow.rag_enabled" />
            </label>
            <label>Max Tasks im Flow
              <input type="number" min="1" max="200" [(ngModel)]="config.artifact_flow.max_tasks" [disabled]="!config.artifact_flow.enabled" />
            </label>
            <label>Max Worker-Jobs je Task
              <input type="number" min="1" max="20" [(ngModel)]="config.artifact_flow.max_worker_jobs_per_task" [disabled]="!config.artifact_flow.enabled" />
            </label>
          </div>
          <div class="muted font-sm mt-md">
            Effektiv: {{ config.artifact_flow.enabled ? 'enabled' : 'disabled' }}
            · RAG: {{ config.artifact_flow.rag_enabled ? 'on' : 'off' }}
            · Top-K: {{ config.artifact_flow.rag_top_k }}
            · Max Tasks: {{ config.artifact_flow.max_tasks }}
            · Max Jobs/Task: {{ config.artifact_flow.max_worker_jobs_per_task }}
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        <div class="card card-info mt-lg">
          <h3>Worker Workspace & OpenCode</h3>
          <p class="muted">Steuert den OpenCode-Toolmodus, den Ausfuehrungsmodus und den Root-Pfad fuer scope-basierte Worker-Workspaces mit den Unterordnern artifacts und rag_helper.</p>
          <div class="grid cols-2 mt-lg">
            <label>OpenCode Tool-Modus
              <select [(ngModel)]="config.opencode_runtime.tool_mode">
                <option value="full">full</option>
                <option value="readonly">readonly</option>
                <option value="toolless">toolless</option>
              </select>
            </label>
            <label>OpenCode Ausfuehrungsmodus
              <select [(ngModel)]="config.opencode_runtime.execution_mode">
                <option value="backend">backend</option>
                <option value="live_terminal">live_terminal</option>
                <option value="interactive_terminal">interactive_terminal</option>
              </select>
            </label>
            <label>Interactive Launch-Modus
              <select [(ngModel)]="config.opencode_runtime.interactive_launch_mode">
                <option value="run">run (stabil)</option>
                <option value="tui">tui (experimentell)</option>
              </select>
            </label>
            <label>Workspace Root (optional)
              <input [(ngModel)]="config.worker_runtime.workspace_root" placeholder="z.B. /data/worker-runtime" />
            </label>
            <label>Workspace Reuse Scope
              <select [(ngModel)]="config.worker_runtime.workspace_reuse_mode">
                <option value="goal_worker">goal_worker (Session/Files fortsetzen)</option>
                <option value="task">task (isoliert pro Task)</option>
              </select>
            </label>
          </div>
          <div class="muted font-sm mt-md">
            Effektiv: Tool-Modus {{ config.opencode_runtime.tool_mode }} · Ausfuehrung {{ config.opencode_runtime.execution_mode }} · Launch {{ config.opencode_runtime.interactive_launch_mode }} · Workspace Root {{ config.worker_runtime.workspace_root || '(default: data/worker-runtime)' }} · Reuse {{ config.worker_runtime.workspace_reuse_mode || 'goal_worker' }}
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        <div class="card card-info mt-lg">
          <h3>Ollama Strategie fuer Hub, Scrum-Rollen und OpenCode</h3>
          <p class="muted">Leitet aus den aktuell im Ollama-Katalog sichtbaren Modellen eine projektgeeignete Zuordnung fuer Hub-Planung, Scrum-/Worker-Rollen, Task-Kinds und template-nahe Flows ab.</p>
          <div class="grid cols-2 mt-lg">
            <div>
              <div class="muted">Empfohlener Hub-Planer</div>
              <div>{{ getProjectModelRoutingRecommendation().hub_copilot_model || '-' }}</div>
            </div>
            <div>
              <div class="muted">Empfohlener Coding-Worker</div>
              <div>{{ getProjectModelRoutingRecommendation().task_kind_model_overrides.coding || '-' }}</div>
            </div>
            <div>
              <div class="muted">Empfohlener Review-/Analyse-Pfad</div>
              <div>{{ getProjectModelRoutingRecommendation().task_kind_model_overrides.review || '-' }}</div>
            </div>
            <div>
              <div class="muted">Empfohlener Doku-/Template-Pfad</div>
              <div>{{ getProjectModelRoutingRecommendation().task_kind_model_overrides.documentation || '-' }}</div>
            </div>
          </div>
          @if (getProjectModelRoutingRecommendation().warnings.length) {
            <div class="danger font-sm mt-md">
              @for (warning of getProjectModelRoutingRecommendation().warnings; track warning) {
                <div>{{ warning }}</div>
              }
            </div>
          }
          <div class="row mt-md gap-sm">
            <button class="button-outline" (click)="applyProjectModelRoutingRecommendation()">Empfohlene Zuordnung uebernehmen</button>
          </div>
          @if (getOllamaModelStrategyRows().length) {
            <table class="standard-table mt-lg">
              <thead>
                <tr>
                  <th>Ollama-Modell</th>
                  <th>Klasse</th>
                  <th>Empfohlene Nutzung</th>
                  <th>OpenCode-Worker</th>
                  <th>Hinweis</th>
                </tr>
              </thead>
              <tbody>
                @for (row of getOllamaModelStrategyRows(); track row.id) {
                  <tr>
                    <td class="font-mono font-sm">{{ row.id }}</td>
                    <td>{{ row.category }}</td>
                    <td>{{ row.recommended_use }}</td>
                    <td>{{ row.opencode_worker }}</td>
                    <td>{{ row.note }}</td>
                  </tr>
                }
              </tbody>
            </table>
          } @else {
            <div class="muted font-sm mt-md">Noch keine Ollama-Modelle im Provider-Katalog erkannt.</div>
          }
        </div>
        <div class="card mt-lg">
          <h3>Routing Overrides fuer Rollen, Templates und Task-Kinds</h3>
          <p class="muted">Task-Kind-Overrides greifen global. Role- und Template-Overrides matchen case-insensitiv auf exakte Namen und ergaenzen die bestehende Hub/Worker-Orchestrierung ohne neue Nebenpfade.</p>
          <div class="grid cols-2 mt-lg">
            <label>Task-Kind planning
              <select [ngModel]="getTaskKindModelOverride('planning')" (ngModelChange)="setTaskKindModelOverride('planning', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
            <label>Task-Kind analysis
              <select [ngModel]="getTaskKindModelOverride('analysis')" (ngModelChange)="setTaskKindModelOverride('analysis', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
            <label>Task-Kind research
              <select [ngModel]="getTaskKindModelOverride('research')" (ngModelChange)="setTaskKindModelOverride('research', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
            <label>Task-Kind coding
              <select [ngModel]="getTaskKindModelOverride('coding')" (ngModelChange)="setTaskKindModelOverride('coding', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
            <label>Task-Kind testing
              <select [ngModel]="getTaskKindModelOverride('testing')" (ngModelChange)="setTaskKindModelOverride('testing', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
            <label>Task-Kind review
              <select [ngModel]="getTaskKindModelOverride('review')" (ngModelChange)="setTaskKindModelOverride('review', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
            <label>Task-Kind documentation
              <select [ngModel]="getTaskKindModelOverride('documentation')" (ngModelChange)="setTaskKindModelOverride('documentation', $event)">
                <option value="">Default / Hub-Fallback</option>
                @for (m of getCatalogModels('ollama'); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}</option>
                }
              </select>
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <label class="col-span-full">Role Overrides (JSON)
              <textarea [(ngModel)]="roleModelOverridesRaw" rows="8" class="font-mono w-full" [class.input-error]="roleModelOverridesError"></textarea>
              @if (roleModelOverridesError) {
                <span class="error-text">{{ roleModelOverridesError }}</span>
              }
            </label>
            <label class="col-span-full">Template Overrides (JSON)
              <textarea [(ngModel)]="templateModelOverridesRaw" rows="8" class="font-mono w-full" [class.input-error]="templateModelOverridesError"></textarea>
              @if (templateModelOverridesError) {
                <span class="error-text">{{ templateModelOverridesError }}</span>
              }
            </label>
          </div>
          <div class="muted font-sm mt-md">
            Beispiel Rolle: <span class="font-mono">&#123;"backend developer": "lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2:latest"&#125;</span><br />
            Beispiel Template: <span class="font-mono">&#123;"documentation": "lmstudio-community-ministral-3-3b-instruct-2512-gguf-ministral-3-3b-instruct-2512-q4_k_m:latest"&#125;</span>
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Research Backends</h3>
          <p class="muted">Optionaler Research-Pfad fuer tiefergehende Recherchen. Die Hub-Orchestrierung bleibt unveraendert; hier wird nur das ausfuehrende Backend konfiguriert und transparent gemacht.</p>
          <div class="grid cols-2">
            <label>Aktiver Provider
              <select [(ngModel)]="config.research_backend.provider">
                @for (provider of getSupportedResearchProviders(); track provider) {
                  <option [value]="provider">{{ provider }}</option>
                }
              </select>
            </label>
            <label>Mode
              <input [(ngModel)]="config.research_backend.mode" placeholder="cli" />
            </label>
            <label class="col-span-full">Command
              <input [(ngModel)]="config.research_backend.command" placeholder="z.B. python main.py &#123;prompt&#125;" />
            </label>
            <label>Working Dir
              <input [(ngModel)]="config.research_backend.working_dir" placeholder="optional, z.B. /opt/deer-flow" />
            </label>
            <label>Timeout (s)
              <input type="number" min="30" max="7200" [(ngModel)]="config.research_backend.timeout_seconds" />
            </label>
          </div>
          <label class="row gap-sm mt-md">
            <input type="checkbox" [(ngModel)]="config.research_backend.enabled" />
            Research-Backend aktivieren
          </label>
          <div class="muted font-sm mt-sm">
            Ergebnisformat: {{ config?.research_backend?.result_format || 'markdown' }}
          </div>
          @if (getResearchBackendWarnings().length) {
            <div class="danger font-sm mt-md">
              @for (warning of getResearchBackendWarnings(); track warning) {
                <div>{{ warning }}</div>
              }
            </div>
          }
          <div class="grid cols-2 mt-md">
            @for (entry of getResearchBackendPreflightEntries(); track entry.provider) {
              <div class="card card-info">
                <div class="row flex-between gap-sm">
                  <strong>{{ entry.display_name || entry.provider }}</strong>
                  <span>{{ entry.selected ? 'aktiv' : 'optional' }}</span>
                </div>
                <div class="muted font-sm mt-sm">
                  Enabled: <strong>{{ entry.enabled ? 'yes' : 'no' }}</strong> · Configured: <strong>{{ entry.configured ? 'yes' : 'no' }}</strong>
                </div>
                <div class="muted font-sm mt-sm">
                  Binary: <strong>{{ entry.binary_available ? 'ok' : 'missing' }}</strong> · Working Dir: <strong>{{ entry.working_dir_exists ? 'ok' : (entry.working_dir ? 'missing' : 'not set') }}</strong>
                </div>
                <div class="muted status-text-sm mt-sm">
                  {{ entry.command || entry.install_hint || '-' }}
                </div>
              </div>
            }
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Hub LLM Defaults</h3>
          <div class="grid cols-2">
            <label>Default Provider
              <select [(ngModel)]="config.default_provider" (ngModelChange)="ensureProviderModelConsistency()">
                @for (group of getProviderSelectGroups(); track group.label) {
                  <optgroup [label]="group.label">
                    @for (p of group.providers; track p.id) {
                      <option [value]="p.id">
                        {{ p.id }}{{ p.available ? '' : ' (offline)' }}{{ p.model_count ? ' [' + p.model_count + ']' : '' }}
                      </option>
                    }
                  </optgroup>
                }
              </select>
            </label>
            <label>Default Model
              <select [(ngModel)]="config.default_model">
                @for (m of getCatalogModels(getEffectiveProvider()); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}{{ m.context_length ? ' (ctx ' + m.context_length + ')' : '' }}</option>
                }
                @if ((config?.default_model || '').trim() && !isCurrentModelInCatalog()) {
                  <option [value]="config.default_model">{{ config.default_model }} (custom)</option>
                }
              </select>
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <label>LM Studio URL
              <input [(ngModel)]="config.lmstudio_url" placeholder="z.B. http://127.0.0.1:1234/v1">
            </label>
            <label>OpenAI URL
              <input [(ngModel)]="config.openai_url">
            </label>
            <label>Anthropic URL
              <input [(ngModel)]="config.anthropic_url">
            </label>
          </div>
          <div class="muted font-sm mt-sm">
            Provider-Ziel: {{ getProviderEndpointSummary(getEffectiveProvider()) }}
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Codex CLI Runtime</h3>
          <p class="muted">Explizite Runtime fuer das Codex-CLI-Backend. Fuer lokale Modelle mit LM Studio kann hier ein OpenAI-kompatibler Endpoint gesetzt werden, auch wenn der globale Default-Provider anders ist.</p>
          <div class="grid cols-2">
            <label>Ziel-Provider
              <select [(ngModel)]="config.codex_cli.target_provider">
                <option value="">Automatisch / Fallback</option>
                <option value="lmstudio">lmstudio</option>
                @for (backend of getConfiguredLocalBackends(); track backend.provider) {
                  <option [value]="backend.provider">{{ backend.provider }}{{ backend.name ? ' (' + backend.name + ')' : '' }}</option>
                }
              </select>
            </label>
            <label>Base URL
              <input [(ngModel)]="config.codex_cli.base_url" placeholder="z.B. http://127.0.0.1:1234/v1" />
            </label>
            <label>API Key Profil
              <input [(ngModel)]="config.codex_cli.api_key_profile" placeholder="z.B. codex-local" />
            </label>
          </div>
          <label class="row gap-sm mt-md">
            <input type="checkbox" [(ngModel)]="config.codex_cli.prefer_lmstudio" />
            LM Studio bevorzugen, wenn keine Base URL gesetzt ist
          </label>
          <div class="muted font-sm mt-sm">
            Effektives Ziel: {{ getCodexCliTargetSummary() }}
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Lokale OpenAI-kompatible Backends</h3>
          <p class="muted">Zusatz-Runtimes wie vLLM, LiteLLM oder ein lokales Gateway koennen hier fuer Routing, Codex CLI und Provider-Auswahl gepflegt werden.</p>
          @if (!getConfiguredLocalBackends().length) {
            <div class="muted font-sm">Noch keine zusaetzlichen lokalen Backends konfiguriert.</div>
          }
          <div class="grid gap-md">
            @for (backend of getConfiguredLocalBackends(); track backend.provider; let index = $index) {
              <div class="card card-info">
                <div class="row flex-between gap-sm">
                  <strong>{{ backend.provider || 'local-backend' }}</strong>
                  <button class="button-outline" (click)="removeLocalOpenAiBackend(index)">Entfernen</button>
                </div>
                <div class="grid cols-2 mt-md">
                  <label>ID / Provider
                    <input [(ngModel)]="backend.provider" placeholder="z.B. vllm_local" />
                  </label>
                  <label>Anzeigename
                    <input [(ngModel)]="backend.name" placeholder="z.B. vLLM Local" />
                  </label>
                  <label>Base URL
                    <input [(ngModel)]="backend.base_url" placeholder="z.B. http://127.0.0.1:8010/v1" />
                  </label>
                  <label>API-Key Profil
                    <input [(ngModel)]="backend.api_key_profile" placeholder="optional" />
                  </label>
                  <label class="col-span-full">Modelle (Komma-getrennt)
                    <input [(ngModel)]="backend.models_text" placeholder="qwen2.5-coder, deepseek-coder" />
                  </label>
                </div>
                <label class="row gap-sm mt-md">
                  <input type="checkbox" [(ngModel)]="backend.supports_tool_calls" />
                  Tool Calls / Function Calling verfuegbar
                </label>
                <div class="muted font-sm mt-sm">
                  Effektive URL: {{ backend.base_url || '(nicht gesetzt)' }}
                </div>
              </div>
            }
          </div>
          <div class="row mt-md gap-sm">
            <button class="button-outline" (click)="addLocalOpenAiBackend()">Backend hinzufuegen</button>
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Pro-Agent LLM Defaults</h3>
          <p class="muted">Schnellansicht und Bearbeitung der LLM-Konfiguration je Agent.</p>
          <div class="row gap-sm mb-md">
            <button class="button-outline" (click)="loadAgentLlmConfigs()">Liste aktualisieren</button>
          </div>
          <table class="standard-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Temp</th>
                <th>Ctx</th>
                <th>API-Profil</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (a of allAgents; track a.name) {
                <tr>
                  <td>{{ a.name }} <span class="muted">({{ a.role }})</span></td>
                  <td>
                    <select [(ngModel)]="getAgentLlmDraft(a.name).provider">
                      <option value="ollama">ollama</option>
                      <option value="lmstudio">lmstudio</option>
                      <option value="openai">openai</option>
                      <option value="codex">codex</option>
                      <option value="anthropic">anthropic</option>
                    </select>
                  </td>
                  <td><input [(ngModel)]="getAgentLlmDraft(a.name).model" /></td>
                  <td>
                    <input type="number" min="0" max="2" step="0.1" [(ngModel)]="getAgentLlmDraft(a.name).temperature" />
                  </td>
                  <td>
                    <input type="number" min="256" step="1" [(ngModel)]="getAgentLlmDraft(a.name).context_limit" />
                  </td>
                  <td><input [(ngModel)]="getAgentLlmDraft(a.name).api_key_profile" placeholder="optional" /></td>
                  <td>
                    <button (click)="saveAgentLlmConfig(a.name)">Speichern</button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>API-Key Profile</h3>
          <p class="muted">Wiederverwendbare API-Key Profile fuer Agenten (z.B. openai/codex).</p>
          <textarea [(ngModel)]="llmApiKeyProfilesRaw" rows="8" class="font-mono w-full" [class.input-error]="llmApiKeyProfilesError"></textarea>
          @if (llmApiKeyProfilesError) {
            <span class="error-text">{{ llmApiKeyProfilesError }}</span>
          }
          <div class="row mt-sm">
            <button (click)="saveApiKeyProfiles()" [disabled]="llmApiKeyProfilesError">Profile speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Benchmark Konfiguration <span class="help-icon" [appTooltip]="'Einstellungen fuer LLM-Performance-Benchmarks und Modell-Auswahl.'" tabindex="0">?</span></h3>
          <p class="muted">Aktive Retention- und Fallback-Regeln fuer Modell-Benchmarkdaten.</p>
          <div class="grid cols-2">
            <label>
              Retention max_days <span class="help-icon" [appTooltip]="'Maximale Aufbewahrungszeit fuer Benchmark-Daten in Tagen.'" tabindex="0">?</span>
              <input type="number" min="1" max="3650" [(ngModel)]="benchmarkRetentionDays" [class.input-error]="benchmarkRetentionDays < 1 || benchmarkRetentionDays > 3650" />
              @if (benchmarkRetentionDays < 1 || benchmarkRetentionDays > 3650) {
                <span class="error-text">Wert muss zwischen 1 und 3650 liegen</span>
              }
            </label>
            <label>
              Retention max_samples
              <input type="number" min="50" max="50000" [(ngModel)]="benchmarkRetentionSamples" [class.input-error]="benchmarkRetentionSamples < 50 || benchmarkRetentionSamples > 50000" />
              @if (benchmarkRetentionSamples < 50 || benchmarkRetentionSamples > 50000) {
                <span class="error-text">Wert muss zwischen 50 und 50000 liegen</span>
              }
            </label>
            <label class="col-span-full">
              Provider precedence (Komma-getrennt)
              <input [(ngModel)]="benchmarkProviderOrderTextValue" placeholder="proposal_backend, routing_effective_backend, llm_config_provider, default_provider, provider" />
            </label>
            <label class="col-span-full">
              Model precedence (Komma-getrennt)
              <input [(ngModel)]="benchmarkModelOrderTextValue" placeholder="proposal_model, llm_config_model, default_model, model" />
            </label>
          </div>
          @if (benchmarkValidationError) {
            <div class="danger font-sm mt-sm">{{ benchmarkValidationError }}</div>
          }
          @if (benchmarkConfig) {
            <details class="mt-md">
              <summary>Aktive Defaults anzeigen</summary>
              <pre class="preformatted">{{ benchmarkConfig?.defaults | json }}</pre>
            </details>
          } @else {
            <div class="muted mt-sm">Keine Benchmark-Config verfuegbar.</div>
          }
          <div class="muted font-sm mt-sm">
            Vorschau Provider: <span class="font-mono">{{ benchmarkProviderOrderText() }}</span><br />
            Vorschau Model: <span class="font-mono">{{ benchmarkModelOrderText() }}</span>
          </div>
          <div class="row mt-md gap-sm">
            <button (click)="saveBenchmarkConfig()" [disabled]="benchmarkRetentionDays < 1 || benchmarkRetentionDays > 3650 || benchmarkRetentionSamples < 50 || benchmarkRetentionSamples > 50000">Speichern</button>
            <button class="button-outline" (click)="loadBenchmarkConfig()">Aktualisieren</button>
          </div>
          @if (benchmarkConfig) {
            <details class="mt-md">
              <summary>Rohdaten anzeigen</summary>
              <pre class="preformatted">{{ benchmarkConfig | json }}</pre>
            </details>
          }
        </div>
        }
        @if (selectedSection === 'system') {
        <div class="card">
          <h3>System Parameter</h3>
          <div class="grid cols-2">
            <label>Log Level
              <select [(ngModel)]="config.log_level">
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </label>
            <label>Agent Offline Timeout (s)
              <input type="number" [(ngModel)]="config.agent_offline_timeout" min="10" [class.input-error]="config.agent_offline_timeout < 10">
              @if (config.agent_offline_timeout < 10) {
                <span class="error-text">Mindestens 10 Sekunden</span>
              }
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <label>HTTP Timeout (s)
              <input type="number" [(ngModel)]="config.http_timeout" min="1" [class.input-error]="config.http_timeout < 1">
              @if (config.http_timeout < 1) {
                <span class="error-text">Mindestens 1 Sekunde</span>
              }
            </label>
            <label>Command Timeout (s)
              <input type="number" [(ngModel)]="config.command_timeout" min="1" [class.input-error]="config.command_timeout < 1">
              @if (config.command_timeout < 1) {
                <span class="error-text">Mindestens 1 Sekunde</span>
              }
            </label>
          </div>
          <div class="row mt-lg">
            <button (click)="save()" [disabled]="config.agent_offline_timeout < 10 || config.http_timeout < 1 || config.command_timeout < 1">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'quality') {
        <div class="card">
          <h3>Qualitaetsregeln <span class="help-icon" [appTooltip]="'Qualitaetspruefungen fuer Task-Ausgaben. Prueft Mindestlaenge und optionale Marker.'" tabindex="0">?</span></h3>
          <p class="muted">Qualitaetsregeln fuer Task-Ausgaben und Autopilot-Durchsetzung.</p>
          <div class="grid cols-2">
            <label class="row gap-sm">
              <input type="checkbox" [(ngModel)]="qgEnabled" />
              Gates aktiviert <span class="help-icon" [appTooltip]="'Aktiviert oder deaktiviert alle Quality-Gate-Pruefungen global.'" tabindex="0">?</span>
            </label>
            <label class="row gap-sm">
              <input type="checkbox" [(ngModel)]="qgAutopilotEnforce" />
              Im Autopilot erzwingen <span class="help-icon" [appTooltip]="'Wenn aktiviert, schlaegt ein Task im Autopilot fehl, wenn er die Qualitaetsregeln nicht besteht.'" tabindex="0">?</span>
            </label>
            <label>
              Min. Output Zeichen
              <input type="number" min="1" [(ngModel)]="qgMinOutputChars" [class.input-error]="qgMinOutputChars < 1" />
              @if (qgMinOutputChars < 1) {
                <span class="error-text">Mindestens 1 Zeichen</span>
              }
            </label>
            <label>
              Coding Keywords (Komma)
              <input [(ngModel)]="qgCodingKeywordsText" placeholder="code, implement, test" />
            </label>
            <label class="col-span-full">
              Erforderliche Marker bei Coding (Komma)
              <input [(ngModel)]="qgMarkersText" placeholder="pytest, passed, success" />
            </label>
          </div>
          <div class="row mt-md gap-sm">
            <button class="secondary" (click)="loadQualityGates()">Reload</button>
            <button (click)="saveQualityGates()" [disabled]="qgMinOutputChars < 1">Qualitaetsregeln speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'system') {
        <div class="card">
          <h3>Roh-Konfiguration (Hub)</h3>
          <p class="muted font-sm">Vorsicht: Direkte Bearbeitung der config.json des Hubs.</p>
          <textarea [(ngModel)]="configRaw" rows="10" class="font-mono w-full" [class.input-error]="configRawError"></textarea>
          @if (configRawError) {
            <span class="error-text">{{ configRawError }}</span>
          }
          <div class="row mt-sm">
            <button (click)="saveRaw()" class="button-outline" [disabled]="configRawError">Roh-Daten Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm' && llmHistory && llmHistory.length > 0) {
          <div class="card">
            <h3>LMStudio Modell-Historie</h3>
            <p class="muted">Zuletzt verwendete oder verfügbare Modelle von LMStudio.</p>
            <table class="standard-table">
              <thead>
                <tr>
                  <th>Modell ID</th>
                  <th>Zuletzt gesehen</th>
                </tr>
              </thead>
              <tbody>
                @for (h of llmHistory; track h) {
                  <tr>
                    <td class="font-mono font-sm">{{ h.model || h.id }}</td>
                    <td class="font-sm">{{ h.last_seen || '-' }}</td>
                  </tr>
                }
              </tbody>
            </table>
            <div class="row mt-lg">
              <button (click)="loadHistory()" class="button-outline">Aktualisieren</button>
            </div>
          </div>
        }
      </div>
    }
    `
})
export class SettingsComponent implements OnInit {
  private api = inject(AgentApiService);
  private system = inject(SystemFacade);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);

  hub = this.system.resolveHubAgent();
  allAgents = this.system.listConfiguredAgents();
  config: any = createDefaultSettingsConfig();
  configRaw = '';
  llmHistory: any[] = [];
  isAdmin = false;
  isDarkMode = document.body.classList.contains('dark-mode');
  qgEnabled = true;
  qgAutopilotEnforce = true;
  qgMinOutputChars = 8;
  qgCodingKeywordsText = 'code, implement, fix, refactor, bug, test, feature, endpoint';
  qgMarkersText = 'test, pytest, passed, success, lint, ok';
  selectedSection: 'account' | 'llm' | 'quality' | 'system' = 'llm';
  providerCatalog: any = null;
  benchmarkConfig: any = null;
  benchmarkRetentionDays = 90;
  benchmarkRetentionSamples = 2000;
  benchmarkProviderOrderTextValue = '';
  benchmarkModelOrderTextValue = '';
  benchmarkValidationError = '';
  configRawError = '';
  agentLlmDrafts: Record<string, any> = {};
  llmApiKeyProfilesRaw = '{}';
  llmApiKeyProfilesError = '';
  roleModelOverridesRaw = '{}';
  templateModelOverridesRaw = '{}';
  roleModelOverridesError = '';
  templateModelOverridesError = '';
  researchBackendStatus: any = null;

  ngOnInit() {
    this.auth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
    });
    this.load();
    this.loadHistory();
    this.loadProviderCatalog();
    this.loadBenchmarkConfig();
    this.loadAgentLlmConfigs();
  }

  toggleDarkMode() {
    this.isDarkMode = !this.isDarkMode;
    if (this.isDarkMode) {
      document.body.classList.add('dark-mode');
      localStorage.setItem('ananta.dark-mode', 'true');
    } else {
      document.body.classList.remove('dark-mode');
      localStorage.setItem('ananta.dark-mode', 'false');
    }
  }

  setSection(section: 'account' | 'llm' | 'quality' | 'system') {
    this.selectedSection = section;
  }

  load() {
    if (!this.hub) {
        this.hub = this.system.resolveHubAgent();
    }
    this.allAgents = this.system.listConfiguredAgents();
    this.bootstrapAgentLlmDrafts();
    if (!this.hub) return;
    
    this.system.getConfig(this.hub.url).subscribe({
      next: cfg => {
        this.config = {
          ...createDefaultSettingsConfig(),
          ...(cfg && typeof cfg === 'object' ? cfg : {}),
          hub_copilot: normalizeHubCopilotConfigValue(cfg?.hub_copilot),
          context_bundle_policy: normalizeContextBundlePolicyConfigValue(cfg?.context_bundle_policy),
          artifact_flow: normalizeArtifactFlowConfigValue(cfg?.artifact_flow),
          opencode_runtime: normalizeOpencodeRuntimeConfigValue(cfg?.opencode_runtime),
          worker_runtime: normalizeWorkerRuntimeConfigValue(cfg?.worker_runtime),
          role_model_overrides: normalizeModelOverrideMapValue(cfg?.role_model_overrides),
          template_model_overrides: normalizeModelOverrideMapValue(cfg?.template_model_overrides),
          task_kind_model_overrides: normalizeModelOverrideMapValue(cfg?.task_kind_model_overrides),
          research_backend: normalizeResearchBackendConfigValue(cfg?.research_backend),
        };
        if (!this.config.codex_cli || typeof this.config.codex_cli !== 'object') {
          this.config.codex_cli = { target_provider: '', base_url: '', api_key_profile: '', prefer_lmstudio: true };
        } else {
          this.config.codex_cli = {
            target_provider: String(this.config.codex_cli.target_provider || '').trim().toLowerCase(),
            base_url: normalizeOpenAICompatibleBaseUrlValue(this.config.codex_cli.base_url),
            api_key_profile: this.config.codex_cli.api_key_profile || '',
            prefer_lmstudio: this.config.codex_cli.prefer_lmstudio !== false,
          };
        }
        this.config.local_openai_backends = this.normalizeLocalOpenAiBackends(this.config.local_openai_backends);
        this.configRaw = JSON.stringify(cfg, null, 2);
        this.llmApiKeyProfilesRaw = JSON.stringify(cfg?.llm_api_key_profiles || {}, null, 2);
        this.llmApiKeyProfilesError = '';
        this.syncModelOverrideEditorsFromConfig();
        this.syncQualityGatesFromConfig(cfg);
        this.loadProviderCatalog();
        this.loadResearchBackendStatus();
      },
      error: () => this.ns.error('Einstellungen konnten nicht geladen werden')
    });
  }

  private bootstrapAgentLlmDrafts() {
    for (const a of this.allAgents) {
      if (!this.agentLlmDrafts[a.name]) {
        this.agentLlmDrafts[a.name] = {
          provider: 'lmstudio',
          model: '',
          temperature: 0.2,
          context_limit: 4096,
          api_key_profile: ''
        };
      }
    }
  }

  getAgentLlmDraft(agentName: string): any {
    if (!this.agentLlmDrafts[agentName]) {
      this.agentLlmDrafts[agentName] = {
        provider: 'lmstudio',
        model: '',
        temperature: 0.2,
        context_limit: 4096,
        api_key_profile: ''
      };
    }
    return this.agentLlmDrafts[agentName];
  }

  loadAgentLlmConfigs() {
    this.allAgents = this.system.listConfiguredAgents();
    this.bootstrapAgentLlmDrafts();
    for (const a of this.allAgents) {
      this.api.getConfig(a.url).subscribe({
        next: cfg => {
          const llm = (cfg && cfg.llm_config) ? cfg.llm_config : {};
          const provider = String(llm.provider || cfg?.default_provider || 'lmstudio');
          const model = String(llm.model || cfg?.default_model || '');
          const temperature = Number(llm.temperature ?? 0.2);
          const contextLimit = Number(llm.context_limit ?? 4096);
          this.agentLlmDrafts[a.name] = {
            ...this.agentLlmDrafts[a.name],
            provider,
            model,
            temperature: Number.isFinite(temperature) ? temperature : 0.2,
            context_limit: Number.isFinite(contextLimit) ? contextLimit : 4096,
            api_key_profile: String(llm.api_key_profile || '')
          };
        },
        error: () => {}
      });
    }
  }

  saveAgentLlmConfig(agentName: string) {
    const agent = this.allAgents.find(a => a.name === agentName);
    if (!agent) return;
    const draft = this.agentLlmDrafts[agentName] || {};
    const temp = Number(draft.temperature);
    const ctx = Number(draft.context_limit);
    if (!Number.isFinite(temp) || temp < 0 || temp > 2) {
      this.ns.error(`Temperature ungueltig fuer Agent ${agentName}`);
      return;
    }
    if (!Number.isFinite(ctx) || ctx < 256) {
      this.ns.error(`Context Limit ungueltig fuer Agent ${agentName}`);
      return;
    }
    this.api.getConfig(agent.url).subscribe({
      next: cfg => {
        const current = cfg && typeof cfg === 'object' ? cfg : {};
        const nextCfg = {
          ...current,
          llm_config: {
            ...(current.llm_config || {}),
            provider: String(draft.provider || 'lmstudio'),
            model: String(draft.model || ''),
            temperature: temp,
            context_limit: Math.round(ctx),
            api_key_profile: String(draft.api_key_profile || '')
          }
        };
        this.api.setConfig(agent.url, nextCfg).subscribe({
          next: () => this.ns.success(`LLM-Konfiguration gespeichert: ${agentName}`),
          error: () => this.ns.error(`Speichern fehlgeschlagen: ${agentName}`)
        });
      },
      error: () => this.ns.error(`Konfiguration nicht ladbar: ${agentName}`)
    });
  }

  loadProviderCatalog() {
    if (!this.hub) return;
    this.system.listProviderCatalog(this.hub.url).subscribe({
      next: (catalog) => {
        this.providerCatalog = catalog || null;
        this.ensureProviderModelConsistency();
      },
      error: () => {
        this.providerCatalog = null;
      }
    });
  }

  loadBenchmarkConfig() {
    if (!this.hub) return;
    this.system.getLlmBenchmarksConfig(this.hub.url).subscribe({
      next: (cfg) => {
        this.benchmarkConfig = cfg || null;
        this.syncBenchmarkConfigEditor(cfg || {});
        this.benchmarkValidationError = '';
      },
      error: () => {
        this.benchmarkConfig = null;
        this.benchmarkValidationError = '';
      }
    });
  }

  loadHistory() {
    if (!this.hub) return;
    this.system.getLlmHistory(this.hub.url).subscribe({
      next: history => {
        this.llmHistory = history || [];
      },
      error: () => console.warn('Konnte LLM Historie nicht laden')
    });
  }

  save() {
    if (!this.hub) return;
    let roleModelOverrides: Record<string, string> = {};
    let templateModelOverrides: Record<string, string> = {};
    try {
      roleModelOverrides = this.parseModelOverrideEditor(this.roleModelOverridesRaw, 'role');
      templateModelOverrides = this.parseModelOverrideEditor(this.templateModelOverridesRaw, 'template');
    } catch {
      this.ns.error('Model-Override JSON ist ungueltig');
      return;
    }
    this.config = {
      ...(this.config && typeof this.config === 'object' ? this.config : {}),
      hub_copilot: normalizeHubCopilotConfigValue(this.config?.hub_copilot),
      context_bundle_policy: normalizeContextBundlePolicyConfigValue(this.config?.context_bundle_policy),
      artifact_flow: normalizeArtifactFlowConfigValue(this.config?.artifact_flow),
      opencode_runtime: normalizeOpencodeRuntimeConfigValue(this.config?.opencode_runtime),
      worker_runtime: normalizeWorkerRuntimeConfigValue(this.config?.worker_runtime),
      role_model_overrides: roleModelOverrides,
      template_model_overrides: templateModelOverrides,
      task_kind_model_overrides: normalizeModelOverrideMapValue(this.config?.task_kind_model_overrides),
      research_backend: normalizeResearchBackendConfigValue(this.config?.research_backend),
    };
    if (this.config?.codex_cli && typeof this.config.codex_cli === 'object') {
      this.config.codex_cli = {
        ...this.config.codex_cli,
        target_provider: String(this.config.codex_cli.target_provider || '').trim().toLowerCase(),
        base_url: normalizeOpenAICompatibleBaseUrlValue(this.config.codex_cli.base_url),
        api_key_profile: String(this.config.codex_cli.api_key_profile || '').trim(),
        prefer_lmstudio: this.config.codex_cli.prefer_lmstudio !== false,
      };
    }
    this.config.local_openai_backends = this.normalizeLocalOpenAiBackends(this.config?.local_openai_backends);
    this.system.setConfig(this.hub.url, this.config).subscribe({
      next: () => {
        this.ns.success('Einstellungen gespeichert');
        this.load();
        this.loadResearchBackendStatus();
      },
      error: () => this.ns.error('Speichern fehlgeschlagen')
    });
  }

  saveRaw() {
    if (!this.hub) return;
    this.configRawError = '';
    try {
      const cfg = JSON.parse(this.configRaw);
      this.system.setConfig(this.hub.url, cfg).subscribe({
        next: () => {
          this.ns.success('Roh-Konfiguration gespeichert');
          this.load();
        },
        error: () => this.ns.error('Speichern fehlgeschlagen')
      });
    } catch (e) {
      this.configRawError = 'Ungültiges JSON: ' + (e instanceof Error ? e.message : String(e));
    }
  }

  getEffectiveProvider(): string {
    return (this.config?.default_provider || 'ollama').toLowerCase();
  }

  getEffectiveModel(): string {
    const model = this.config?.default_model;
    return model && String(model).trim().length ? model : '(auto)';
  }

  getEffectiveBaseUrl(): string {
    return this.getBaseUrlForProvider(this.getEffectiveProvider());
  }

  getHubCopilotProvider(): string {
    return resolveHubCopilotProviderValue(this.config, this.getEffectiveProvider());
  }

  getHubCopilotModel(): string {
    return resolveHubCopilotModelValue(this.config, this.getEffectiveModel());
  }

  getHubCopilotBaseUrl(): string {
    const explicit = String(this.config?.hub_copilot?.base_url || '').trim();
    if (explicit) return normalizeOpenAICompatibleBaseUrlValue(explicit);
    const llmProvider = String(this.config?.llm_config?.provider || '').trim().toLowerCase();
    if (llmProvider && llmProvider === this.getHubCopilotProvider() && this.config?.llm_config?.base_url) {
      return normalizeOpenAICompatibleBaseUrlValue(this.config.llm_config.base_url);
    }
    return this.getBaseUrlForProvider(this.getHubCopilotProvider());
  }

  getHubCopilotProviderSource(): string {
    return resolveHubCopilotProviderSourceValue(this.config);
  }

  getHubCopilotModelSource(): string {
    return resolveHubCopilotModelSourceValue(this.config);
  }

  isHubCopilotActive(): boolean {
    return this.config?.hub_copilot?.enabled === true && !!this.getHubCopilotProvider() && !!this.getHubCopilotModel();
  }

  getEffectiveContextBundlePolicy(): any {
    return resolveContextBundlePolicyValue(this.config);
  }

  getOllamaCatalogModelIds(): string[] {
    return this.getCatalogModels('ollama').map((model) => model.id);
  }

  getOllamaModelStrategyRows(): OllamaStrategyRow[] {
    return buildOllamaModelStrategyRowsValue(this.getOllamaCatalogModelIds());
  }

  getProjectModelRoutingRecommendation(): ProjectModelRoutingRecommendation {
    return buildProjectModelRoutingRecommendationValue(this.getOllamaCatalogModelIds());
  }

  applyProjectModelRoutingRecommendation() {
    const recommendation = this.getProjectModelRoutingRecommendation();
    this.config.default_provider = recommendation.default_provider;
    this.config.default_model = recommendation.default_model;
    this.config.hub_copilot = {
      ...normalizeHubCopilotConfigValue(this.config?.hub_copilot),
      enabled: true,
      provider: recommendation.hub_copilot_provider,
      model: recommendation.hub_copilot_model,
    };
    this.config.task_kind_model_overrides = { ...recommendation.task_kind_model_overrides };
    this.config.role_model_overrides = { ...recommendation.role_model_overrides };
    this.config.template_model_overrides = { ...recommendation.template_model_overrides };
    this.syncModelOverrideEditorsFromConfig();
  }

  getTaskKindModelOverride(taskKind: string): string {
    return String(this.config?.task_kind_model_overrides?.[String(taskKind || '').trim().toLowerCase()] || '').trim();
  }

  setTaskKindModelOverride(taskKind: string, modelId: string) {
    const normalizedTaskKind = String(taskKind || '').trim().toLowerCase();
    if (!normalizedTaskKind) return;
    const normalizedModel = String(modelId || '').trim();
    this.config.task_kind_model_overrides = normalizeModelOverrideMapValue(this.config?.task_kind_model_overrides);
    if (!normalizedModel) {
      delete this.config.task_kind_model_overrides[normalizedTaskKind];
      return;
    }
    this.config.task_kind_model_overrides[normalizedTaskKind] = normalizedModel;
  }

  requiresApiKey(provider: string): boolean {
    return provider === 'openai' || provider === 'codex' || provider === 'anthropic';
  }

  getProviderEndpointSummary(provider: string): string {
    const normalizedProvider = String(provider || '').trim().toLowerCase();
    if (normalizedProvider === 'codex') {
      return `Provider API: ${this.getBaseUrlForProvider(normalizedProvider)} | CLI: ${this.getCodexCliEffectiveBaseUrl()}`;
    }
    return this.getBaseUrlForProvider(normalizedProvider);
  }

  getProviderRuntimeKind(provider: string): string {
    const p = String(provider || '').trim().toLowerCase();
    const baseUrl = this.getBaseUrlForProvider(p);
    if (p === 'lmstudio' || p === 'ollama' || this.getConfiguredLocalBackends().some((entry) => entry.provider === p)) return 'local runtime';
    if (p === 'codex') return this.isProbablyLocalUrl(this.getCodexCliEffectiveBaseUrl()) ? 'local openai-compatible' : 'cloud/openai-compatible';
    if (this.isProbablyLocalUrl(baseUrl)) return 'local openai-compatible';
    return 'cloud provider';
  }

  getCodexCliEffectiveBaseUrl(): string {
    const codexCfg = this.config?.codex_cli || {};
    const targetProvider = String(codexCfg?.target_provider || '').trim().toLowerCase();
    if (targetProvider) {
      const localBackend = this.getConfiguredLocalBackends().find((entry) => entry.provider === targetProvider);
      if (targetProvider === 'lmstudio') {
        return this.normalizeOpenAICompatibleBaseUrl(this.config?.lmstudio_url || 'http://192.168.56.1:1234/v1');
      }
      if (localBackend?.base_url) return this.normalizeOpenAICompatibleBaseUrl(localBackend.base_url);
    }
    if (codexCfg?.base_url) return this.normalizeOpenAICompatibleBaseUrl(codexCfg.base_url);
    if (codexCfg?.prefer_lmstudio !== false) return this.normalizeOpenAICompatibleBaseUrl(this.config?.lmstudio_url || 'http://192.168.56.1:1234/v1');
    return this.normalizeOpenAICompatibleBaseUrl(this.config?.openai_url || 'https://api.openai.com/v1/chat/completions');
  }

  getCodexCliTargetSummary(): string {
    const url = this.getCodexCliEffectiveBaseUrl();
    const runtime = this.isProbablyLocalUrl(url) ? 'local openai-compatible' : 'cloud/openai-compatible';
    const targetProvider = String(this.config?.codex_cli?.target_provider || '').trim().toLowerCase();
    return `${runtime}${targetProvider ? ` via ${targetProvider}` : ''} (${url})`;
  }

  getLlmConfigurationWarnings(): string[] {
    const warnings: string[] = [];
    const provider = this.getEffectiveProvider();
    const effectiveBaseUrl = this.getEffectiveBaseUrl();
    const providerBlock = this.getCatalogProviders().find((entry) => entry.id === provider);
    if (providerBlock && !providerBlock.available) {
      warnings.push(`Provider ${provider} ist laut Katalog aktuell nicht verfuegbar.`);
    }
    if (provider === 'lmstudio') {
      if (!String(this.config?.lmstudio_url || '').trim()) {
        warnings.push('LM Studio ist Default-Provider, aber die LM-Studio-URL ist nicht gesetzt.');
      } else if (!this.isProbablyLocalUrl(effectiveBaseUrl)) {
        warnings.push('LM Studio ist als lokaler Standard gesetzt, die konfigurierte URL wirkt jedoch nicht lokal.');
      }
    }
    if (provider === 'ollama' && !this.isProbablyLocalUrl(effectiveBaseUrl)) {
      warnings.push('Ollama sollte auf eine lokale Runtime zeigen, die aktuelle URL wirkt jedoch nicht lokal.');
    }
    if (this.requiresApiKey(provider) && !this.hasApiKey(provider)) {
      warnings.push(`Provider ${provider} benoetigt einen API-Key oder ein passendes Profil.`);
    }

    const codexUrl = this.getCodexCliEffectiveBaseUrl();
    const codexProfile = String(this.config?.codex_cli?.api_key_profile || '').trim();
    const codexTargetProvider = String(this.config?.codex_cli?.target_provider || '').trim().toLowerCase();
    if (codexTargetProvider && codexTargetProvider !== 'lmstudio' && !this.getConfiguredLocalBackends().some((entry) => entry.provider === codexTargetProvider)) {
      warnings.push(`Codex CLI target_provider ${codexTargetProvider} ist nicht in local_openai_backends konfiguriert.`);
    }
    if (!codexUrl) {
      warnings.push('Codex CLI hat kein effektives Ziel; setzen Sie codex_cli.base_url oder aktivieren Sie LM Studio als Fallback.');
    }
    if (!this.isProbablyLocalUrl(codexUrl) && !codexProfile && !this.hasApiKey('codex')) {
      warnings.push('Codex CLI zeigt auf eine Cloud/OpenAI-kompatible Runtime, aber weder API-Key-Profil noch globaler Key sind erkennbar.');
    }
    return warnings;
  }

  loadResearchBackendStatus() {
    if (!this.hub) return;
    if (!this.api || typeof this.api.sgptBackends !== 'function') {
      this.researchBackendStatus = null;
      return;
    }
    this.api.sgptBackends(this.hub.url).subscribe({
      next: (data) => {
        this.researchBackendStatus = data?.preflight || null;
      },
      error: () => {
        this.researchBackendStatus = null;
      }
    });
  }

  getSupportedResearchProviders(): string[] {
    const providers = this.researchBackendStatus?.research_backends;
    if (providers && typeof providers === 'object') {
      const names = Object.keys(providers).filter((entry) => !!String(entry || '').trim());
      if (names.length) return names;
    }
    return ['deerflow', 'ananta_research'];
  }

  getResearchBackendPreflightEntries(): any[] {
    const providers = this.researchBackendStatus?.research_backends;
    if (!providers || typeof providers !== 'object') return [];
    return Object.values(providers) as any[];
  }

  getResearchBackendWarnings(): string[] {
    const warnings: string[] = [];
    const current = normalizeResearchBackendConfigValue(this.config?.research_backend);
    const selected = (this.researchBackendStatus?.research_backends || {})?.[current.provider] || null;
    if (current.enabled && !String(current.command || '').trim()) {
      warnings.push(`Research-Backend ${current.provider} ist aktiviert, aber ohne command konfiguriert.`);
    }
    if (current.enabled && selected && selected.binary_available === false) {
      warnings.push(`Research-Backend ${current.provider} ist aktiviert, aber das konfigurierte Binary ist aktuell nicht verfuegbar.`);
    }
    if (current.enabled && selected && selected.working_dir && selected.working_dir_exists === false) {
      warnings.push(`Research-Backend ${current.provider} verwendet ein fehlendes working_dir: ${selected.working_dir}`);
    }
    return warnings;
  }

  private getBaseUrlForProvider(provider: string): string {
    const normalizedProvider = String(provider || '').trim().toLowerCase();
    const llmCfg = this.config?.llm_config || {};
    if (llmCfg?.provider === normalizedProvider && llmCfg?.base_url) {
      return this.normalizeOpenAICompatibleBaseUrl(llmCfg.base_url);
    }
    const localBackend = this.getConfiguredLocalBackends().find((entry) => entry.provider === normalizedProvider);
    if (localBackend?.base_url) {
      return this.normalizeOpenAICompatibleBaseUrl(localBackend.base_url);
    }
    const providerDefaults: Record<string, string> = {
      ollama: 'http://localhost:11434/api/generate',
      lmstudio: 'http://192.168.56.1:1234/v1',
      openai: 'https://api.openai.com/v1/chat/completions',
      codex: 'https://api.openai.com/v1/chat/completions',
      anthropic: 'https://api.anthropic.com/v1/messages'
    };
    const key = `${normalizedProvider}_url`;
    return this.normalizeOpenAICompatibleBaseUrl(this.config?.[key] || providerDefaults[normalizedProvider] || '(nicht gesetzt)');
  }

  private isProbablyLocalUrl(url: string): boolean {
    const raw = String(url || '').trim().toLowerCase();
    if (!raw) return false;
    return ['localhost', '127.0.0.1', 'host.docker.internal', '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.'].some(marker => raw.includes(marker));
  }

  private normalizeOpenAICompatibleBaseUrl(url: any): string {
    return normalizeOpenAICompatibleBaseUrlValue(url);
  }

  private syncModelOverrideEditorsFromConfig() {
    this.config.role_model_overrides = normalizeModelOverrideMapValue(this.config?.role_model_overrides);
    this.config.template_model_overrides = normalizeModelOverrideMapValue(this.config?.template_model_overrides);
    this.config.task_kind_model_overrides = normalizeModelOverrideMapValue(this.config?.task_kind_model_overrides);
    this.roleModelOverridesRaw = JSON.stringify(this.config.role_model_overrides, null, 2);
    this.templateModelOverridesRaw = JSON.stringify(this.config.template_model_overrides, null, 2);
    this.roleModelOverridesError = '';
    this.templateModelOverridesError = '';
  }

  private parseModelOverrideEditor(text: string, kind: 'role' | 'template'): Record<string, string> {
    const raw = String(text || '').trim();
    try {
      const parsed = raw ? JSON.parse(raw) : {};
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Root muss ein Objekt sein.');
      }
      const normalized = normalizeModelOverrideMapValue(parsed);
      if (kind === 'role') {
        this.roleModelOverridesError = '';
      } else {
        this.templateModelOverridesError = '';
      }
      return normalized;
    } catch (error) {
      const message = `Ungueltiges JSON: ${error instanceof Error ? error.message : String(error)}`;
      if (kind === 'role') {
        this.roleModelOverridesError = message;
      } else {
        this.templateModelOverridesError = message;
      }
      throw error;
    }
  }

  getConfiguredLocalBackends(): Array<{
    provider: string;
    name: string;
    base_url: string;
    api_key_profile: string;
    models_text: string;
    supports_tool_calls: boolean;
  }> {
    if (!Array.isArray(this.config?.local_openai_backends)) {
      this.config.local_openai_backends = [];
    }
    return this.config.local_openai_backends;
  }

  addLocalOpenAiBackend() {
    this.getConfiguredLocalBackends().push({
      provider: '',
      name: '',
      base_url: '',
      api_key_profile: '',
      models_text: '',
      supports_tool_calls: true,
    });
  }

  removeLocalOpenAiBackend(index: number) {
    this.getConfiguredLocalBackends().splice(index, 1);
  }

  private normalizeLocalOpenAiBackends(items: any): any[] {
    if (!Array.isArray(items)) return [];
    return items
      .map((item) => {
        const provider = String(item?.provider || item?.id || '').trim().toLowerCase();
        const models = this.parseCommaList(item?.models_text ?? item?.models);
        if (!provider) return null;
        return {
          id: provider,
          provider,
          name: String(item?.name || provider).trim(),
          base_url: this.normalizeOpenAICompatibleBaseUrl(item?.base_url),
          api_key_profile: String(item?.api_key_profile || '').trim(),
          models,
          models_text: models.join(', '),
          supports_tool_calls: item?.supports_tool_calls !== false,
        };
      })
      .filter((item): item is any => !!item);
  }

  saveApiKeyProfiles() {
    if (!this.hub) return;
    this.llmApiKeyProfilesError = '';
    let parsed: any = {};
    try {
      parsed = JSON.parse(this.llmApiKeyProfilesRaw || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Root muss ein Objekt sein.');
      }
    } catch (e) {
      this.llmApiKeyProfilesError = 'Ungueltiges JSON: ' + (e instanceof Error ? e.message : String(e));
      return;
    }
    this.system.setConfig(this.hub.url, { llm_api_key_profiles: parsed }).subscribe({
      next: () => {
        this.ns.success('API-Key Profile gespeichert');
        this.load();
      },
      error: () => this.ns.error('API-Key Profile konnten nicht gespeichert werden')
    });
  }

  hasApiKey(provider: string): boolean {
    const llmCfg = this.config?.llm_config || {};
    if (llmCfg?.provider === provider && llmCfg?.api_key) return true;
    if (provider === 'openai' || provider === 'codex') return Boolean(this.config?.openai_api_key);
    if (provider === 'anthropic') return Boolean(this.config?.anthropic_api_key);
    return false;
  }

  getCatalogProviders(): Array<{ id: string; available: boolean; model_count: number }> {
    const providers = Array.isArray(this.providerCatalog?.providers) ? this.providerCatalog.providers : [];
    if (!providers.length) {
      return [
        { id: 'ollama', available: true, model_count: 0 },
        { id: 'lmstudio', available: true, model_count: 0 },
        { id: 'openai', available: true, model_count: 0 },
        { id: 'codex', available: true, model_count: 0 },
        { id: 'anthropic', available: true, model_count: 0 },
        ...this.getConfiguredLocalBackends().map((backend) => ({
          id: backend.provider,
          available: Boolean(backend.base_url),
          model_count: this.parseCommaList(backend.models_text).length,
        })),
      ];
    }
    return providers
      .map((p: any) => ({
        id: String(p?.provider || ''),
        available: !!p?.available,
        model_count: Number(p?.model_count || 0),
      }))
      .filter((p) => !!p.id);
  }

  getProviderSelectGroups(): Array<{ label: string; providers: Array<{ id: string; available: boolean; model_count: number }> }> {
    const providers = this.getCatalogProviders();
    const localIds = new Set(['lmstudio', 'ollama', ...this.getConfiguredLocalBackends().map((entry) => entry.provider)]);
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

  getRuntimeGroupSummary(kind: 'local' | 'cloud' | 'cli'): string {
    if (kind === 'cli') {
      return `codex_cli -> ${this.getCodexCliTargetSummary()}`;
    }
    const providers = this.getCatalogProviders().filter((provider) => {
      const runtimeKind = this.getProviderRuntimeKind(provider.id);
      return kind === 'local' ? runtimeKind.startsWith('local') : !runtimeKind.startsWith('local');
    });
    if (!providers.length) {
      return '-';
    }
    return providers.map((provider) => `${provider.id}${provider.available ? '' : ' (offline)'}`).join(', ');
  }

  getCatalogModels(providerId: string): Array<{ id: string; display_name: string; context_length: number | null }> {
    const providers = Array.isArray(this.providerCatalog?.providers) ? this.providerCatalog.providers : [];
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
      .filter((m) => !!m.id);
  }

  ensureProviderModelConsistency() {
    const provider = this.getEffectiveProvider();
    const models = this.getCatalogModels(provider);
    if (!models.length) return;
    const current = String(this.config?.default_model || '').trim();
    const matched = findMatchingCatalogModelId(current, models);
    if (matched) {
      this.config.default_model = matched;
      return;
    }
    if (!current || !models.some(m => m.id === current)) {
      this.config.default_model = models[0].id;
    }
  }

  ensureHubCopilotModelConsistency() {
    const provider = this.getHubCopilotProvider();
    const models = this.getCatalogModels(provider);
    if (!models.length) return;
    const current = String(this.config?.hub_copilot?.model || '').trim();
    const matched = findMatchingCatalogModelId(current, models);
    if (matched) {
      this.config.hub_copilot.model = matched;
      return;
    }
    if (!current || !models.some(m => m.id === current)) {
      this.config.hub_copilot.model = models[0].id;
    }
  }

  isCurrentModelInCatalog(): boolean {
    const provider = this.getEffectiveProvider();
    const models = this.getCatalogModels(provider);
    const current = String(this.config?.default_model || '').trim();
    if (!current || !models.length) return false;
    return !!findMatchingCatalogModelId(current, models);
  }

  isHubCopilotCurrentModelInCatalog(): boolean {
    const provider = this.getHubCopilotProvider();
    const models = this.getCatalogModels(provider);
    const current = String(this.config?.hub_copilot?.model || '').trim();
    if (!current || !models.length) return false;
    return !!findMatchingCatalogModelId(current, models);
  }

  benchmarkProviderOrderText(): string {
    const arr = this.parseCommaList(this.benchmarkProviderOrderTextValue);
    return Array.isArray(arr) && arr.length ? arr.join(' -> ') : '-';
  }

  benchmarkModelOrderText(): string {
    const arr = this.parseCommaList(this.benchmarkModelOrderTextValue);
    return Array.isArray(arr) && arr.length ? arr.join(' -> ') : '-';
  }

  saveBenchmarkConfig() {
    if (!this.hub) return;
    this.benchmarkValidationError = '';

    const providerOrder = this.parseCommaList(this.benchmarkProviderOrderTextValue);
    const modelOrder = this.parseCommaList(this.benchmarkModelOrderTextValue);
    const providerAllowed = new Set(['proposal_backend', 'routing_effective_backend', 'llm_config_provider', 'default_provider', 'provider']);
    const modelAllowed = new Set(['proposal_model', 'llm_config_model', 'default_model', 'model']);

    const invalidProviderKeys = providerOrder.filter((k) => !providerAllowed.has(k));
    const invalidModelKeys = modelOrder.filter((k) => !modelAllowed.has(k));
    if (invalidProviderKeys.length || invalidModelKeys.length) {
      const invalidMsg = [
        invalidProviderKeys.length ? `ungueltige provider_order keys: ${invalidProviderKeys.join(', ')}` : '',
        invalidModelKeys.length ? `ungueltige model_order keys: ${invalidModelKeys.join(', ')}` : '',
      ]
        .filter(Boolean)
        .join(' | ');
      this.benchmarkValidationError = invalidMsg;
      this.ns.error('Benchmark-Konfiguration ist ungueltig');
      return;
    }

    const days = Math.max(1, Math.min(3650, Number(this.benchmarkRetentionDays || 90)));
    const samples = Math.max(50, Math.min(50000, Number(this.benchmarkRetentionSamples || 2000)));
    this.benchmarkRetentionDays = days;
    this.benchmarkRetentionSamples = samples;

    const payload = {
      benchmark_retention: {
        max_days: days,
        max_samples: samples,
      },
      benchmark_identity_precedence: {
        provider_order: providerOrder,
        model_order: modelOrder,
      },
    };
    this.system.setConfig(this.hub.url, payload).subscribe({
      next: () => {
        this.ns.success('Benchmark-Konfiguration gespeichert');
        this.loadBenchmarkConfig();
      },
      error: () => this.ns.error('Benchmark-Konfiguration konnte nicht gespeichert werden'),
    });
  }

  private parseCommaList(text: string): string[] {
    return String(text || '')
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);
  }

  private normalizeHubCopilotConfig(value: any): any {
    return normalizeHubCopilotConfigValue(value);
  }

  private syncBenchmarkConfigEditor(cfg: any) {
    const retention = cfg?.retention || {};
    const precedence = cfg?.identity_precedence || {};
    this.benchmarkRetentionDays = Number(retention.max_days || 90);
    this.benchmarkRetentionSamples = Number(retention.max_samples || 2000);
    const providerOrder = Array.isArray(precedence.provider_order) ? precedence.provider_order : [];
    const modelOrder = Array.isArray(precedence.model_order) ? precedence.model_order : [];
    this.benchmarkProviderOrderTextValue = providerOrder.join(', ');
    this.benchmarkModelOrderTextValue = modelOrder.join(', ');
  }

  private syncQualityGatesFromConfig(cfg: any) {
    const qg = (cfg && cfg.quality_gates) ? cfg.quality_gates : {};
    this.qgEnabled = qg.enabled !== false;
    this.qgAutopilotEnforce = qg.autopilot_enforce !== false;
    this.qgMinOutputChars = Number(qg.min_output_chars || 8);
    this.qgCodingKeywordsText = Array.isArray(qg.coding_keywords) ? qg.coding_keywords.join(', ') : this.qgCodingKeywordsText;
    this.qgMarkersText = Array.isArray(qg.required_output_markers_for_coding)
      ? qg.required_output_markers_for_coding.join(', ')
      : this.qgMarkersText;
  }

  loadQualityGates() {
    if (!this.hub) return;
    this.system.getConfig(this.hub.url).subscribe({
      next: cfg => this.syncQualityGatesFromConfig(cfg),
      error: () => this.ns.error('Quality-Gates konnten nicht geladen werden')
    });
  }

  saveQualityGates() {
    if (!this.hub) return;
    const toList = (text: string) =>
      (text || '')
        .split(',')
        .map(v => v.trim())
        .filter(Boolean);
    const payload = {
      quality_gates: {
        enabled: !!this.qgEnabled,
        autopilot_enforce: !!this.qgAutopilotEnforce,
        min_output_chars: Math.max(1, Number(this.qgMinOutputChars || 8)),
        coding_keywords: toList(this.qgCodingKeywordsText),
        required_output_markers_for_coding: toList(this.qgMarkersText),
      }
    };
    this.system.setConfig(this.hub.url, payload).subscribe({
      next: () => {
        this.ns.success('Quality-Gates gespeichert');
        this.load();
      },
      error: () => this.ns.error('Quality-Gates konnten nicht gespeichert werden')
    });
  }
}
