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
import { buildOllamaModelStrategyRowsValue, buildProjectModelRoutingRecommendationValue, createDefaultSettingsConfig, findMatchingCatalogModelId, normalizeArtifactFlowConfigValue, normalizeContextBundlePolicyConfigValue, normalizeHubCopilotConfigValue, normalizeModelOverrideMapValue, normalizeOpencodeRuntimeConfigValue, normalizeOpenAICompatibleBaseUrlValue, normalizeResearchBackendConfigValue, normalizeWorkerRuntimeConfigValue, resolveContextBundlePolicyValue, resolveHubCopilotModelSourceValue, resolveHubCopilotModelValue, resolveHubCopilotProviderSourceValue, resolveHubCopilotProviderValue, type OllamaStrategyRow, type ProjectModelRoutingRecommendation } from './settings-config.helpers';
export { buildOllamaModelStrategyRowsValue, buildProjectModelRoutingRecommendationValue, findMatchingCatalogModelId, normalizeArtifactFlowConfigValue, normalizeContextBundlePolicyConfigValue, normalizeHubCopilotConfigValue, normalizeModelOverrideMapValue, normalizeOpencodeRuntimeConfigValue, normalizeOpenAICompatibleBaseUrlValue, normalizeResearchBackendConfigValue, normalizeWorkerRuntimeConfigValue, resolveContextBundlePolicyValue, resolveHubCopilotModelSourceValue, resolveHubCopilotModelValue, resolveHubCopilotProviderSourceValue, resolveHubCopilotProviderValue, type OllamaStrategyRow, type ProjectModelRoutingRecommendation } from './settings-config.helpers';
@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [FormsModule, JsonPipe, ChangePasswordComponent, UserManagementComponent, MfaSetupComponent, TooltipDirective],
  templateUrl: './settings.component.html'
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
  evolutionProviderStatus: any = null;
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
          sgpt_routing: this.normalizeSgptRouting(cfg?.sgpt_routing),
          approval_lifecycle: this.normalizeApprovalLifecycle(cfg?.approval_lifecycle),
          mutation_gate: this.normalizeMutationGate(cfg?.mutation_gate),
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
        this.loadEvolutionProviderStatus();
      },
      error: () => this.ns.error('Einstellungen konnten nicht geladen werden')
    });
  }
  normalizeSgptRouting(raw: any): any {
    const defaults = {
      default_backend: 'ananta-worker',
      task_kind_backend: { coding: 'ananta-worker', analysis: 'ananta-worker', doc: 'ananta-worker', ops: 'ananta-worker', research: 'deerflow' },
    };
    if (!raw || typeof raw !== 'object') return defaults;
    return { ...defaults, ...raw, task_kind_backend: { ...defaults.task_kind_backend, ...(raw.task_kind_backend || {}) } };
  }
  normalizeApprovalLifecycle(raw: any): any {
    const defaults = { enabled: false, grant_one_shot: true, default_ttl_seconds: 3600, goal_pre_approvals: { enabled: false, ttl_seconds: 7200 } };
    if (!raw || typeof raw !== 'object') return defaults;
    return { ...defaults, ...raw, goal_pre_approvals: { ...defaults.goal_pre_approvals, ...(raw.goal_pre_approvals || {}) } };
  }
  normalizeMutationGate(raw: any): any {
    if (!raw || typeof raw !== 'object') return { enabled: true, global_deny_mutations: false };
    return { enabled: raw.enabled !== false, global_deny_mutations: !!raw.global_deny_mutations };
  }
  taskKindBackendOptions(): string[] {
    return ['ananta-worker', 'deerflow', 'codex', 'opencode', 'aider', 'sgpt'];
  }
  taskKindRoutingEntries(): { kind: string }[] {
    const routing = this.config?.sgpt_routing?.task_kind_backend || {};
    return Object.keys(routing).map(kind => ({ kind }));
  }
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
      sgpt_routing: this.normalizeSgptRouting(this.config?.sgpt_routing),
      approval_lifecycle: this.normalizeApprovalLifecycle(this.config?.approval_lifecycle),
      mutation_gate: this.normalizeMutationGate(this.config?.mutation_gate),
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
    const providers = this.evolutionProviderStatus?.providers;
    return Array.isArray(providers) ? providers : [];
  }

  getEvolutionModeSummary(): string {
    const cfg = this.getEvolutionConfig();
    if (!cfg.enabled) return 'disabled';
    if (cfg.analyze_only) return 'analyze_only';
    if (!cfg.apply_allowed) return 'proposal_review';
    return 'controlled_apply';
  }

  getEvolutionConfig(): any {
    const cfg = this.evolutionProviderStatus?.config;
    return cfg && typeof cfg === 'object'
      ? cfg
      : {
          enabled: false,
          analyze_only: true,
          validate_allowed: false,
          apply_allowed: false,
          require_review_before_apply: true,
        };
  }

  getEvolutionWarnings(): string[] {
    const warnings: string[] = [];
    const cfg = this.getEvolutionConfig();
    if (!cfg.enabled) {
      warnings.push('Evolution ist global deaktiviert.');
      return warnings;
    }
    if (cfg.apply_allowed === true && cfg.require_review_before_apply !== true) {
      warnings.push('Apply ist freigegeben, aber Review vor Apply ist nicht erzwungen.');
    }
    if (cfg.apply_allowed === true && cfg.analyze_only === true) {
      warnings.push('Apply ist global freigegeben, aber Provider koennen weiter analyze-only fail-closed bleiben.');
    }
    if (cfg.validate_allowed !== true) {
      warnings.push('Validation ist aktuell nicht global freigegeben.');
    }
    for (const provider of this.getEvolutionProviders()) {
      const apply = provider?.capability_matrix?.apply;
      const validate = provider?.capability_matrix?.validate;
      if (apply?.supported && !apply?.available && apply?.fail_closed_reason) {
        warnings.push(`Provider ${provider.provider_name} blockiert Apply: ${apply.fail_closed_reason}`);
      }
      if (validate?.supported && !validate?.available && validate?.fail_closed_reason) {
        warnings.push(`Provider ${provider.provider_name} blockiert Validate: ${validate.fail_closed_reason}`);
      }
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
