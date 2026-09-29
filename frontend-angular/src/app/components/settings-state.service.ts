import { ChangeDetectorRef, Directive, OnInit, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { SystemFacade } from '../features/system/system.facade';
import { DashboardFeatureFlagStore } from '../features/dashboard-foundation/dashboard-feature-flags';
import {
  buildOllamaModelStrategyRowsValue, buildProjectModelRoutingRecommendationValue,
  createDefaultSettingsConfig,
  CONTEXT_WINDOW_PROFILES, contextWindowConfigError, contextWindowSummaryRows,
  normalizeContextWindowConfigValue, normalizeContextWindowDraftValue,
  normalizeHubCopilotConfigValue, normalizeModelOverrideMapValue,
  normalizeOpenAICompatibleBaseUrlValue,
  resolveContextBundlePolicyValue, resolveHubCopilotModelSourceValue,
  resolveHubCopilotModelValue, resolveHubCopilotProviderSourceValue,
  resolveHubCopilotProviderValue, type OllamaStrategyRow,
  type ProjectModelRoutingRecommendation,
} from './settings-config.helpers';
import {
  buildNormalizedSettingsSections, normalizeApprovalLifecycleValue,
  normalizeAutopilotSecurityPoliciesValue, normalizeKnowledgeContextValue,
  normalizeLoadedCodexCliValue, normalizeLocalAiValue, normalizeMutationGateValue,
  normalizeObjValue, normalizeSavedCodexCliValue, normalizeSgptRoutingValue,
} from './settings-config-sections.helpers';
import {
  classifyProviderRuntimeValue, collectLlmConfigurationWarnings,
  createEmptyLocalOpenAiBackend, describeCodexCliTargetValue,
  groupProvidersForSelectValue, hasApiKeyValue, isModelInCatalogValue,
  listCatalogModelsValue, listCatalogProvidersValue, normalizeLocalOpenAiBackendsValue,
  parseCommaListValue, requiresApiKeyValue, resolveBaseUrlForProviderValue,
  resolveCodexCliEffectiveBaseUrlValue, resolveConsistentCatalogModelId,
  type CatalogModelEntry, type CatalogProviderEntry, type LocalOpenAiBackendDraft,
} from './settings-provider-runtime.helpers';
import {
  collectEvolutionWarnings, collectResearchBackendWarnings,
  listEvolutionProvidersValue, listResearchBackendPreflightEntriesValue,
  listSupportedResearchProvidersValue, resolveEvolutionConfigValue,
  summarizeEvolutionModeValue,
} from './settings-backend-status.helpers';
import {
  agentLlmDraftError, agentLlmDraftFromConfig, benchmarkEditorStateFromConfig,
  benchmarkOrderValidationError, buildAgentLlmConfigPayload, buildBenchmarkConfigPayload,
  buildQualityGatesPayload, clampBenchmarkRetentionDays, clampBenchmarkRetentionSamples,
  createDefaultAgentLlmDraft, formatBenchmarkOrderText, invalidJsonMessage,
  parseJsonObjectText, parseModelOverrideText, qualityGateEditorStateFromConfig,
} from './settings-editor-payloads.helpers';

export type SettingsSection = 'account' | 'llm' | 'models' | 'quality' | 'voice' | 'network' | 'system' | 'erweitert';

export function settingsSectionFromQuery(value: string | null): SettingsSection | null {
  const sections: SettingsSection[] = ['account', 'llm', 'models', 'quality', 'voice', 'network', 'system', 'erweitert'];
  return sections.includes(value as SettingsSection) ? value as SettingsSection : null;
}

/**
 * Stateful facade behind the settings page: owns the editable form state and the
 * hub I/O. Normalization, provider resolution, status projections and editor
 * payload mapping live in the pure `settings-*.helpers.ts` modules it delegates to.
 */
@Directive()
export class SettingsState implements OnInit {
  readonly dashboardFeatures = inject(DashboardFeatureFlagStore);
  private api = inject(AgentApiService);
  private system = inject(SystemFacade);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);
  private route = inject(ActivatedRoute);
  private changeDetector = inject(ChangeDetectorRef);
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
  selectedSection: SettingsSection = 'llm';
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
  evolutionProviderStatus: any = null;
  private systemFormDirty = false;
  ngOnInit() {
    const requestedSection = settingsSectionFromQuery(this.route.snapshot.queryParamMap.get('section'));
    if (requestedSection) this.selectedSection = requestedSection;
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
  setSection(section: SettingsSection) {
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
        const loadedConfig: any = {
          ...createDefaultSettingsConfig(),
          ...(cfg && typeof cfg === 'object' ? cfg : {}),
          ...buildNormalizedSettingsSections(cfg, {
            context_window: normalizeContextWindowDraftValue(cfg?.context_window),
            role_model_overrides: normalizeModelOverrideMapValue(cfg?.role_model_overrides),
            template_model_overrides: normalizeModelOverrideMapValue(cfg?.template_model_overrides),
          }),
        };
        if (this.systemFormDirty) {
          loadedConfig.log_level = this.config?.log_level ?? loadedConfig.log_level;
          loadedConfig.agent_offline_timeout = this.config?.agent_offline_timeout ?? loadedConfig.agent_offline_timeout;
          loadedConfig.http_timeout = this.config?.http_timeout ?? loadedConfig.http_timeout;
          loadedConfig.command_timeout = this.config?.command_timeout ?? loadedConfig.command_timeout;
        }
        this.config = loadedConfig;
        this.config.codex_cli = normalizeLoadedCodexCliValue(this.config.codex_cli);
        this.config.local_openai_backends = normalizeLocalOpenAiBackendsValue(this.config.local_openai_backends);
        this.configRaw = JSON.stringify(cfg, null, 2);
        this.llmApiKeyProfilesRaw = JSON.stringify(cfg?.llm_api_key_profiles || {}, null, 2);
        this.llmApiKeyProfilesError = '';
        this.syncModelOverrideEditorsFromConfig();
        this.syncQualityGatesFromConfig(cfg);
        this.loadProviderCatalog();
        this.loadResearchBackendStatus();
        this.loadEvolutionProviderStatus();
        this.changeDetector?.markForCheck();
      },
      error: () => this.ns.error('Einstellungen konnten nicht geladen werden')
    });
  }
  markSystemFormDirty() {
    this.systemFormDirty = true;
  }
  normalizeSgptRouting(raw: any): any {
    return normalizeSgptRoutingValue(raw);
  }
  normalizeApprovalLifecycle(raw: any): any {
    return normalizeApprovalLifecycleValue(raw);
  }
  normalizeMutationGate(raw: any): any {
    return normalizeMutationGateValue(raw);
  }
  taskKindBackendOptions(): string[] {
    return ['ananta-worker', 'deerflow', 'codex', 'opencode', 'aider', 'sgpt'];
  }
  taskKindRoutingEntries(): { kind: string }[] {
    const routing = this.config?.sgpt_routing?.task_kind_backend || {};
    return Object.keys(routing).map(kind => ({ kind }));
  }
  normalizeObj(raw: any, defaults: Record<string, any>): any {
    return normalizeObjValue(raw, defaults);
  }
  normalizeLocalAi(raw: any): any {
    return normalizeLocalAiValue(raw);
  }
  normalizeAutopilotSecurityPolicies(raw: any): any {
    return normalizeAutopilotSecurityPoliciesValue(raw);
  }
  normalizeKnowledgeContext(raw: any): any {
    return normalizeKnowledgeContextValue(raw);
  }
  workerParallelismOptions(): number[] { return [1, 2, 3, 4, 6, 8]; }
  getRuntimeProfileOptions(): string[] {
    const catalog = this.config?.runtime_profile_effective?.catalog;
    if (catalog && typeof catalog === 'object') {
      return Object.keys(catalog).sort();
    }
    // Fallback: legacy baseline profiles.
    return ['local-dev', 'trusted-lab', 'compose-safe', 'distributed-strict'];
  }
  getGovernanceModeOptions(): string[] {
    const catalog = this.config?.governance_mode_effective?.catalog;
    if (catalog && typeof catalog === 'object') {
      return Object.keys(catalog).sort();
    }
    return ['safe', 'balanced', 'strict'];
  }
  private bootstrapAgentLlmDrafts() {
    for (const a of this.allAgents) {
      if (!this.agentLlmDrafts[a.name]) {
        this.agentLlmDrafts[a.name] = createDefaultAgentLlmDraft();
      }
    }
  }
  getAgentLlmDraft(agentName: string): any {
    if (!this.agentLlmDrafts[agentName]) {
      this.agentLlmDrafts[agentName] = createDefaultAgentLlmDraft();
    }
    return this.agentLlmDrafts[agentName];
  }
  loadAgentLlmConfigs() {
    this.allAgents = this.system.listConfiguredAgents();
    this.bootstrapAgentLlmDrafts();
    for (const a of this.allAgents) {
      this.api.getConfig(a.url).subscribe({
        next: cfg => {
          this.agentLlmDrafts[a.name] = {
            ...this.agentLlmDrafts[a.name],
            ...agentLlmDraftFromConfig(cfg),
          };
          this.changeDetector?.markForCheck();
        },
        error: () => {}
      });
    }
  }
  saveAgentLlmConfig(agentName: string) {
    const agent = this.allAgents.find(a => a.name === agentName);
    if (!agent) return;
    const draft = this.agentLlmDrafts[agentName] || {};
    const draftError = agentLlmDraftError(agentName, draft);
    if (draftError) {
      this.ns.error(draftError);
      return;
    }
    const temperature = Number(draft.temperature);
    const contextLimit = Number(draft.context_limit);
    this.api.getConfig(agent.url).subscribe({
      next: cfg => {
        const nextCfg = buildAgentLlmConfigPayload(cfg, draft, temperature, contextLimit);
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
        this.changeDetector?.markForCheck();
      },
      error: () => {
        this.providerCatalog = null;
        this.changeDetector?.markForCheck();
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
    const contextWindowError = contextWindowConfigError(this.config?.context_window);
    if (contextWindowError) {
      this.ns.error(contextWindowError);
      return;
    }
    this.config = {
      ...(this.config && typeof this.config === 'object' ? this.config : {}),
      ...buildNormalizedSettingsSections(this.config, {
        context_window: normalizeContextWindowConfigValue(this.config?.context_window),
        role_model_overrides: roleModelOverrides,
        template_model_overrides: templateModelOverrides,
      }),
    };
    if (this.config?.codex_cli && typeof this.config.codex_cli === 'object') {
      this.config.codex_cli = normalizeSavedCodexCliValue(this.config.codex_cli);
    }
    this.config.local_openai_backends = normalizeLocalOpenAiBackendsValue(this.config?.local_openai_backends);
    // computed by the hub on read (configured/detected/effective window), never persisted
    const { context_window_effective: _contextWindowEffective, ...configToSave } = this.config;
    this.system.setConfig(this.hub.url, configToSave).subscribe({
      next: () => {
        this.ns.success('Einstellungen gespeichert');
        this.systemFormDirty = false;
        this.load();
        this.loadResearchBackendStatus();
        this.loadEvolutionProviderStatus();
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

  readonly contextWindowProfileOptions: Array<{ value: string; label: string }> = [
    { value: '', label: 'Aus Umgebung (ANANTA_CONTEXT_PROFILE / ANANTA_CONTEXT_TOKENS)' },
    { value: 'standard_32k', label: `32k – standard_32k (${CONTEXT_WINDOW_PROFILES['standard_32k']} Tokens, empfohlen)` },
    { value: 'full_64k', label: `64k – full_64k (${CONTEXT_WINDOW_PROFILES['full_64k']} Tokens)` },
    { value: 'extended_128k', label: `128k – extended_128k (${CONTEXT_WINDOW_PROFILES['extended_128k']} Tokens)` },
    { value: 'compact_12k', label: `12k – compact_12k (${CONTEXT_WINDOW_PROFILES['compact_12k']} Tokens)` },
    { value: 'custom', label: 'Custom (eigene Tokenzahl)' },
  ];

  ensureContextWindowDraft(): { profile: string; tokens: number | null } {
    if (!this.config) this.config = {};
    const current = this.config.context_window;
    // keep the bound object stable across change detection; normalize only foreign shapes
    if (!current || typeof current !== 'object' || !('profile' in current)) {
      this.config.context_window = normalizeContextWindowDraftValue(current);
    }
    return this.config.context_window;
  }

  getContextWindowSummaryRows(): Array<{ label: string; value: string }> {
    return contextWindowSummaryRows(this.config?.context_window_effective);
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
    return requiresApiKeyValue(provider);
  }

  getProviderEndpointSummary(provider: string): string {
    const normalizedProvider = String(provider || '').trim().toLowerCase();
    if (normalizedProvider === 'codex') {
      return `Provider API: ${this.getBaseUrlForProvider(normalizedProvider)} | CLI: ${this.getCodexCliEffectiveBaseUrl()}`;
    }
    return this.getBaseUrlForProvider(normalizedProvider);
  }

  getProviderRuntimeKind(provider: string): string {
    const baseUrl = this.getBaseUrlForProvider(String(provider || '').trim().toLowerCase());
    return classifyProviderRuntimeValue(provider, baseUrl, this.getCodexCliEffectiveBaseUrl(), this.getConfiguredLocalBackends());
  }

  getCodexCliEffectiveBaseUrl(): string {
    return resolveCodexCliEffectiveBaseUrlValue(this.config, this.getConfiguredLocalBackends());
  }

  getCodexCliTargetSummary(): string {
    return describeCodexCliTargetValue(this.config, this.getCodexCliEffectiveBaseUrl());
  }

  getLlmConfigurationWarnings(): string[] {
    return collectLlmConfigurationWarnings({
      config: this.config,
      provider: this.getEffectiveProvider(),
      effectiveBaseUrl: this.getEffectiveBaseUrl(),
      codexUrl: this.getCodexCliEffectiveBaseUrl(),
      catalogProviders: this.getCatalogProviders(),
      localBackends: this.getConfiguredLocalBackends(),
    });
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
    return listSupportedResearchProvidersValue(this.researchBackendStatus);
  }

  getResearchBackendPreflightEntries(): any[] {
    return listResearchBackendPreflightEntriesValue(this.researchBackendStatus);
  }

  getResearchBackendWarnings(): string[] {
    return collectResearchBackendWarnings(this.config, this.researchBackendStatus);
  }

  loadEvolutionProviderStatus() {
    if (!this.hub || !this.api || typeof this.api.getEvolutionProviders !== 'function') {
      this.evolutionProviderStatus = null;
      return;
    }
    this.api.getEvolutionProviders(this.hub.url).subscribe({
      next: (data) => {
        this.evolutionProviderStatus = data || null;
      },
      error: () => {
        this.evolutionProviderStatus = null;
      }
    });
  }

  getEvolutionProviders(): any[] {
    return listEvolutionProvidersValue(this.evolutionProviderStatus);
  }

  getEvolutionModeSummary(): string {
    return summarizeEvolutionModeValue(this.getEvolutionConfig());
  }

  getEvolutionConfig(): any {
    return resolveEvolutionConfigValue(this.evolutionProviderStatus);
  }

  getEvolutionWarnings(): string[] {
    return collectEvolutionWarnings(this.getEvolutionConfig(), this.getEvolutionProviders());
  }

  private getBaseUrlForProvider(provider: string): string {
    return resolveBaseUrlForProviderValue(this.config, provider, this.getConfiguredLocalBackends());
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

  /** Parses one override editor and records its error message; rethrows on invalid JSON. */
  private parseModelOverrideEditor(text: string, kind: 'role' | 'template'): Record<string, string> {
    let message = '';
    try {
      return parseModelOverrideText(text);
    } catch (error) {
      message = invalidJsonMessage(error);
      throw error;
    } finally {
      if (kind === 'role') {
        this.roleModelOverridesError = message;
      } else {
        this.templateModelOverridesError = message;
      }
    }
  }

  getConfiguredLocalBackends(): LocalOpenAiBackendDraft[] {
    if (!Array.isArray(this.config?.local_openai_backends)) {
      this.config.local_openai_backends = [];
    }
    return this.config.local_openai_backends;
  }

  addLocalOpenAiBackend() {
    this.getConfiguredLocalBackends().push(createEmptyLocalOpenAiBackend());
  }

  removeLocalOpenAiBackend(index: number) {
    this.getConfiguredLocalBackends().splice(index, 1);
  }

  saveApiKeyProfiles() {
    if (!this.hub) return;
    this.llmApiKeyProfilesError = '';
    let parsed: any = {};
    try {
      parsed = parseJsonObjectText(this.llmApiKeyProfilesRaw || '{}');
    } catch (e) {
      this.llmApiKeyProfilesError = invalidJsonMessage(e);
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
    return hasApiKeyValue(this.config, provider);
  }

  getCatalogProviders(): CatalogProviderEntry[] {
    const hasCatalog = Array.isArray(this.providerCatalog?.providers) && this.providerCatalog.providers.length > 0;
    // local backends only feed the offline fallback list; avoid touching config otherwise
    return listCatalogProvidersValue(this.providerCatalog, hasCatalog ? [] : this.getConfiguredLocalBackends());
  }

  getProviderSelectGroups(): Array<{ label: string; providers: CatalogProviderEntry[] }> {
    const providers = this.getCatalogProviders();
    return groupProvidersForSelectValue(providers, this.getConfiguredLocalBackends());
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

  getCatalogModels(providerId: string): CatalogModelEntry[] {
    return listCatalogModelsValue(this.providerCatalog, providerId);
  }

  ensureProviderModelConsistency() {
    const next = resolveConsistentCatalogModelId(this.config?.default_model, this.getCatalogModels(this.getEffectiveProvider()));
    if (next) this.config.default_model = next;
  }

  ensureHubCopilotModelConsistency() {
    const next = resolveConsistentCatalogModelId(this.config?.hub_copilot?.model, this.getCatalogModels(this.getHubCopilotProvider()));
    if (next) this.config.hub_copilot.model = next;
  }

  isCurrentModelInCatalog(): boolean {
    return isModelInCatalogValue(this.config?.default_model, this.getCatalogModels(this.getEffectiveProvider()));
  }

  isHubCopilotCurrentModelInCatalog(): boolean {
    return isModelInCatalogValue(this.config?.hub_copilot?.model, this.getCatalogModels(this.getHubCopilotProvider()));
  }

  benchmarkProviderOrderText(): string {
    return formatBenchmarkOrderText(this.benchmarkProviderOrderTextValue);
  }

  benchmarkModelOrderText(): string {
    return formatBenchmarkOrderText(this.benchmarkModelOrderTextValue);
  }

  saveBenchmarkConfig() {
    if (!this.hub) return;
    this.benchmarkValidationError = '';

    const providerOrder = parseCommaListValue(this.benchmarkProviderOrderTextValue);
    const modelOrder = parseCommaListValue(this.benchmarkModelOrderTextValue);
    const invalidMsg = benchmarkOrderValidationError(providerOrder, modelOrder);
    if (invalidMsg) {
      this.benchmarkValidationError = invalidMsg;
      this.ns.error('Benchmark-Konfiguration ist ungueltig');
      return;
    }

    const days = clampBenchmarkRetentionDays(this.benchmarkRetentionDays);
    const samples = clampBenchmarkRetentionSamples(this.benchmarkRetentionSamples);
    this.benchmarkRetentionDays = days;
    this.benchmarkRetentionSamples = samples;

    const payload = buildBenchmarkConfigPayload(days, samples, providerOrder, modelOrder);
    this.system.setConfig(this.hub.url, payload).subscribe({
      next: () => {
        this.ns.success('Benchmark-Konfiguration gespeichert');
        this.loadBenchmarkConfig();
      },
      error: () => this.ns.error('Benchmark-Konfiguration konnte nicht gespeichert werden'),
    });
  }

  private parseCommaList(text: string): string[] {
    return parseCommaListValue(text);
  }

  private normalizeHubCopilotConfig(value: any): any {
    return normalizeHubCopilotConfigValue(value);
  }

  private syncBenchmarkConfigEditor(cfg: any) {
    const state = benchmarkEditorStateFromConfig(cfg);
    this.benchmarkRetentionDays = state.retentionDays;
    this.benchmarkRetentionSamples = state.retentionSamples;
    this.benchmarkProviderOrderTextValue = state.providerOrderText;
    this.benchmarkModelOrderTextValue = state.modelOrderText;
  }

  private syncQualityGatesFromConfig(cfg: any) {
    const state = qualityGateEditorStateFromConfig(cfg, {
      enabled: this.qgEnabled,
      autopilotEnforce: this.qgAutopilotEnforce,
      minOutputChars: this.qgMinOutputChars,
      codingKeywordsText: this.qgCodingKeywordsText,
      markersText: this.qgMarkersText,
    });
    this.qgEnabled = state.enabled;
    this.qgAutopilotEnforce = state.autopilotEnforce;
    this.qgMinOutputChars = state.minOutputChars;
    this.qgCodingKeywordsText = state.codingKeywordsText;
    this.qgMarkersText = state.markersText;
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
    const payload = buildQualityGatesPayload({
      enabled: this.qgEnabled,
      autopilotEnforce: this.qgAutopilotEnforce,
      minOutputChars: this.qgMinOutputChars,
      codingKeywordsText: this.qgCodingKeywordsText,
      markersText: this.qgMarkersText,
    });
    this.system.setConfig(this.hub.url, payload).subscribe({
      next: () => {
        this.ns.success('Quality-Gates gespeichert');
        this.load();
      },
      error: () => this.ns.error('Quality-Gates konnten nicht gespeichert werden')
    });
  }
}
