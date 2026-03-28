var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { ChangeDetectorRef, Component, NgZone, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NavigationEnd, Router } from '@angular/router';
import { filter, forkJoin } from 'rxjs';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { AiAssistantControlsComponent } from './ai-assistant-controls.component';
import { AiAssistantDomainService } from './ai-assistant-domain.service';
import { AiAssistantMessageListComponent } from './ai-assistant-message-list.component';
import { AiAssistantStorageService } from './ai-assistant-storage.service';
let AiAssistantComponent = class AiAssistantComponent {
    constructor() {
        this.dir = inject(AgentDirectoryService);
        this.agentApi = inject(AgentApiService);
        this.hubApi = inject(HubApiService);
        this.ns = inject(NotificationService);
        this.auth = inject(UserAuthService);
        this.domain = inject(AiAssistantDomainService);
        this.storage = inject(AiAssistantStorageService);
        this.router = inject(Router);
        this.zone = inject(NgZone);
        this.cdr = inject(ChangeDetectorRef);
        this.minimized = true;
        this.busy = false;
        this.chatInput = '';
        this.useHybridContext = false;
        this.cliBackend = 'auto';
        this.availableCliBackends = ['auto', 'sgpt', 'codex', 'opencode', 'aider', 'mistral_code'];
        this.cliRuntime = {};
        this.chatHistory = [];
        this.pendingPlanStorageKey = 'ananta.ai-assistant.pending-plan';
        this.historyStorageKey = 'ananta.ai-assistant.history.v1';
        this.dockStateStorageKey = 'ananta.ai-assistant.minimized.v1';
        this.runtimeContext = {
            route: '/',
            agents: [],
            teamsCount: 0,
            templatesCount: 0,
            templatesSummary: [],
            editableSettings: [],
            hasConfig: false,
        };
    }
    get hub() {
        return this.dir.list().find(a => a.role === 'hub') || this.dir.list()[0];
    }
    ngOnInit() {
        this.restoreChatHistory();
        this.restoreDockState();
        if (!this.chatHistory.length) {
            this.chatHistory.push({ role: 'assistant', content: 'Hello. I am your AI assistant.' });
        }
        this.loadCliBackend();
        this.restorePendingPlan();
        this.refreshRuntimeContext();
        this.router.events
            .pipe(filter((e) => e instanceof NavigationEnd))
            .subscribe(() => this.refreshRuntimeContext());
    }
    toggleMinimize() {
        this.minimized = !this.minimized;
        this.persistDockState();
    }
    refreshRuntimeContext() {
        const hub = this.hub;
        const decodedUser = this.auth.decodeTokenPayload(this.auth.token);
        const route = this.router.url || '/';
        const agents = this.dir.list().map(a => ({ name: a.name, role: a.role, url: a.url }));
        const selectedAgentName = route.startsWith('/panel/') ? decodeURIComponent(route.split('/panel/')[1]?.split('?')[0] || '') : undefined;
        const baseCtx = {
            route,
            selectedAgentName,
            userRole: decodedUser?.role,
            userName: decodedUser?.sub,
            agents,
            teamsCount: 0,
            templatesCount: 0,
            templatesSummary: [],
            editableSettings: [],
            hasConfig: false,
        };
        if (!hub) {
            this.runtimeContext = baseCtx;
            return;
        }
        this.hubApi.getAssistantReadModel(hub.url).subscribe({
            next: (res) => {
                const teams = Array.isArray(res?.teams?.items) ? res.teams.items : [];
                const templates = Array.isArray(res?.templates?.items) ? res.templates.items : [];
                const effectiveAgents = Array.isArray(res?.agents?.items)
                    ? res.agents.items.map((a) => ({ name: String(a?.name || ''), role: a?.role, url: String(a?.url || '') })).filter((a) => a.name && a.url)
                    : (Array.isArray(res?.agents) ? res.agents : []);
                const mappedAgents = Array.isArray(effectiveAgents) && effectiveAgents.length ? effectiveAgents : agents;
                this.runtimeContext = {
                    ...baseCtx,
                    agents: mappedAgents,
                    teamsCount: teams.length,
                    templatesCount: templates.length,
                    templatesSummary: this.toTemplateSummary(templates),
                    settingsSummary: res?.settings?.summary || null,
                    editableSettings: this.toEditableSettingsSummary(res?.settings?.editable_inventory),
                    automationSummary: res?.automation || null,
                    hasConfig: !!res?.config?.effective,
                    configSnapshot: this.toCompactConfigSnapshot(res?.config?.effective || {}),
                };
                this.cdr.detectChanges();
            },
            error: () => {
                forkJoin({
                    config: this.agentApi.getConfig(hub.url),
                    teams: this.hubApi.listTeams(hub.url),
                    templates: this.hubApi.listTemplates(hub.url),
                    agents: this.hubApi.listAgents(hub.url),
                }).subscribe({
                    next: (legacyRes) => {
                        const teams = Array.isArray(legacyRes.teams) ? legacyRes.teams : [];
                        const templates = Array.isArray(legacyRes.templates) ? legacyRes.templates : [];
                        const effectiveAgents = Array.isArray(legacyRes.agents)
                            ? legacyRes.agents.map((a) => ({ name: String(a?.name || ''), role: a?.role, url: String(a?.url || '') })).filter((a) => a.name && a.url)
                            : agents;
                        this.runtimeContext = {
                            ...baseCtx,
                            agents: effectiveAgents,
                            teamsCount: teams.length,
                            templatesCount: templates.length,
                            templatesSummary: this.toTemplateSummary(templates),
                            settingsSummary: this.toLegacySettingsSummary(legacyRes.config),
                            editableSettings: [],
                            automationSummary: null,
                            hasConfig: !!legacyRes.config,
                            configSnapshot: this.toCompactConfigSnapshot(legacyRes.config),
                        };
                        this.cdr.detectChanges();
                    },
                    error: () => {
                        this.runtimeContext = baseCtx;
                        this.cdr.detectChanges();
                    }
                });
            }
        });
    }
    sendChat() {
        if (!this.chatInput.trim())
            return;
        const hub = this.hub;
        if (!hub) {
            this.ns.info('Hub agent is not configured.');
            return;
        }
        const userMsg = this.chatInput;
        const history = this.buildHistoryPayload();
        const context = this.buildAssistantRequestContext();
        this.chatHistory.push({ role: 'user', content: userMsg });
        this.persistChatHistory();
        this.chatInput = '';
        this.busy = true;
        const assistantMsg = { role: 'assistant', content: '' };
        this.chatHistory.push(assistantMsg);
        if (this.useHybridContext) {
            const hybridPrompt = `Project context:\n${JSON.stringify(context, null, 2)}\n\nUser request:\n${userMsg}`;
            this.agentApi.sgptExecute(hub.url, hybridPrompt, [], undefined, true, this.cliBackend).subscribe({
                next: r => {
                    this.zone.run(() => {
                        const output = typeof r?.output === 'string' ? r.output : '';
                        assistantMsg.content = output && output.trim() ? output : 'Empty SGPT response';
                        if (typeof r?.backend === 'string' && r.backend) {
                            assistantMsg.cliBackendUsed = r.backend;
                        }
                        if (r?.routing && typeof r.routing === 'object') {
                            assistantMsg.routing = {
                                requestedBackend: r.routing.requested_backend,
                                effectiveBackend: r.routing.effective_backend,
                                reason: r.routing.reason,
                                policyVersion: r.routing.policy_version,
                            };
                        }
                        if (r?.context) {
                            assistantMsg.contextMeta = r.context;
                        }
                        this.lastFailedRequest = undefined;
                        this.agentApi.sgptContext(hub.url, userMsg, undefined, false).subscribe({
                            next: ctx => {
                                this.zone.run(() => {
                                    const chunks = Array.isArray(ctx?.chunks) ? ctx.chunks : [];
                                    assistantMsg.contextSources = chunks.map((c) => ({
                                        engine: c.engine,
                                        source: c.source,
                                        score: c.score
                                    }));
                                    this.cdr.detectChanges();
                                });
                            },
                            error: () => { }
                        });
                        this.checkForSgptCommand(assistantMsg);
                        this.persistChatHistory();
                        this.cdr.detectChanges();
                    });
                },
                error: (e) => {
                    this.zone.run(() => {
                        this.ns.error('Hybrid SGPT failed');
                        assistantMsg.content = 'Error: ' + (e?.error?.message || e?.message || 'Hybrid SGPT failed');
                        assistantMsg.recoverableError = true;
                        this.lastFailedRequest = { mode: 'hybrid', prompt: userMsg };
                        this.busy = false;
                        this.persistChatHistory();
                        this.cdr.detectChanges();
                    });
                },
                complete: () => { this.zone.run(() => { this.busy = false; this.cdr.detectChanges(); }); }
            });
            return;
        }
        this.agentApi.llmGenerate(hub.url, userMsg, null, undefined, { history, context }).subscribe({
            next: r => {
                this.zone.run(() => {
                    const responseText = typeof r?.response === 'string' ? r.response : '';
                    if (r?.requires_confirmation && Array.isArray(r.tool_calls)) {
                        assistantMsg.content = responseText && responseText.trim() ? responseText : 'Pending actions require confirmation.';
                        assistantMsg.requiresConfirmation = true;
                        assistantMsg.toolCalls = r.tool_calls;
                        assistantMsg.pendingPrompt = userMsg;
                        assistantMsg.planRisk = this.assessPlanRisk(r.tool_calls);
                        this.storePendingPlan(assistantMsg);
                    }
                    else if (!responseText || !responseText.trim()) {
                        this.ns.error('Empty LLM response');
                        assistantMsg.content = '';
                    }
                    else {
                        assistantMsg.content = responseText;
                        this.checkForSgptCommand(assistantMsg);
                        this.lastFailedRequest = undefined;
                    }
                    this.persistChatHistory();
                    this.cdr.detectChanges();
                });
            },
            error: (e) => {
                this.zone.run(() => {
                    const code = e?.error?.error;
                    const message = e?.error?.message || e?.message;
                    if (code === 'llm_not_configured') {
                        this.ns.error('LLM is not configured. Configure it in settings.');
                        assistantMsg.content = 'LLM configuration missing.';
                    }
                    else {
                        this.ns.error('AI chat failed');
                        assistantMsg.content = 'Error: ' + (message || 'AI chat failed');
                    }
                    assistantMsg.recoverableError = true;
                    this.lastFailedRequest = { mode: 'chat', prompt: userMsg };
                    this.busy = false;
                    this.persistChatHistory();
                    this.cdr.detectChanges();
                });
            },
            complete: () => { this.zone.run(() => { this.busy = false; this.cdr.detectChanges(); }); }
        });
    }
    confirmAction(msg) {
        const hub = this.hub;
        if (!hub || !msg.toolCalls || msg.toolCalls.length === 0)
            return;
        const prompt = msg.pendingPrompt || '';
        const history = this.buildHistoryPayload();
        const toolCalls = msg.toolCalls;
        this.busy = true;
        msg.requiresConfirmation = false;
        msg.toolCalls = [];
        this.clearPendingPlan();
        this.agentApi.llmGenerate(hub.url, prompt, null, undefined, {
            history,
            context: this.buildAssistantRequestContext(),
            tool_calls: toolCalls,
            confirm_tool_calls: true
        }).subscribe({
            next: r => {
                const summary = toolCalls.map(tc => `- ${this.formatToolName(tc?.name)}: ${this.summarizeToolChanges(tc)}`).join('\n');
                const toolResults = Array.isArray(r?.tool_results) ? r.tool_results : [];
                const resultsText = toolResults.length
                    ? `\n\nTool results:\n${toolResults.map((tr) => `- ${tr?.tool || 'tool'}: ${tr?.success ? 'ok' : 'failed'}${tr?.error ? ` (${tr.error})` : ''}`).join('\n')}`
                    : '';
                const msgText = `${r.response || 'Actions completed.'}\n\nApplied changes:\n${summary}${resultsText}`;
                this.chatHistory.push({ role: 'assistant', content: msgText });
                this.refreshRuntimeContext();
                this.persistChatHistory();
            },
            error: () => {
                this.ns.error('Tool execution failed');
                this.busy = false;
            },
            complete: () => { this.busy = false; }
        });
    }
    cancelAction(msg) {
        msg.requiresConfirmation = false;
        msg.toolCalls = [];
        this.clearPendingPlan();
        this.chatHistory.push({ role: 'assistant', content: 'Pending actions cancelled.' });
        this.persistChatHistory();
    }
    retryLastFailed() {
        if (!this.lastFailedRequest || this.busy)
            return;
        this.chatInput = this.lastFailedRequest.prompt;
        this.sendChat();
    }
    formatToolName(name) {
        return this.domain.formatToolName(name);
    }
    summarizeToolScope(tc) {
        return this.domain.summarizeToolScope(tc);
    }
    summarizeToolImpact(tc) {
        return this.domain.summarizeToolImpact(tc);
    }
    summarizeToolChanges(tc) {
        return this.domain.summarizeToolChanges(tc);
    }
    executeSgpt(msg) {
        const hub = this.hub;
        if (!hub || !msg.sgptCommand)
            return;
        const cmd = msg.sgptCommand;
        msg.sgptCommand = undefined;
        this.busy = true;
        this.agentApi.execute(hub.url, { command: cmd }).subscribe({
            next: r => {
                let resultMsg = '### Execution Output\n';
                if (r.stdout)
                    resultMsg += '```text\n' + r.stdout + '\n```';
                if (r.stderr)
                    resultMsg += '\n### Errors\n```text\n' + r.stderr + '\n```';
                if (!r.stdout && !r.stderr)
                    resultMsg = 'Command executed without output.';
                this.chatHistory.push({ role: 'assistant', content: resultMsg });
                this.persistChatHistory();
            },
            error: (err) => {
                this.ns.error('Execution failed');
                this.chatHistory.push({ role: 'assistant', content: 'Error: ' + (err.error?.error || err.message) });
                this.persistChatHistory();
                this.busy = false;
            },
            complete: () => { this.busy = false; }
        });
    }
    checkForSgptCommand(msg) {
        msg.sgptCommand = this.domain.extractSgptCommand(msg.content);
    }
    previewSource(source) {
        const hub = this.hub;
        if (!hub || !source?.source)
            return;
        source.previewLoading = true;
        source.previewError = undefined;
        source.preview = undefined;
        this.agentApi.sgptSource(hub.url, source.source).subscribe({
            next: r => {
                this.zone.run(() => {
                    source.preview = typeof r?.preview === 'string' ? r.preview : 'No preview available';
                    this.cdr.detectChanges();
                });
            },
            error: (e) => {
                this.zone.run(() => {
                    source.previewError = 'Preview failed: ' + (e?.error?.message || e?.message || 'unknown error');
                    this.cdr.detectChanges();
                });
            },
            complete: () => {
                this.zone.run(() => {
                    source.previewLoading = false;
                    this.cdr.detectChanges();
                });
            }
        });
    }
    async copySourcePath(sourcePath) {
        try {
            await navigator.clipboard.writeText(sourcePath);
            this.ns.success('Source path copied');
        }
        catch {
            this.ns.error('Could not copy source path');
        }
    }
    buildHistoryPayload() {
        const maxItems = 10;
        const history = this.chatHistory.slice(-maxItems);
        return history.map(m => ({ role: m.role, content: m.content }));
    }
    loadCliBackend() {
        const hub = this.hub;
        if (!hub)
            return;
        this.agentApi.sgptBackends(hub.url).subscribe({
            next: data => {
                const supported = Object.keys(data?.supported_backends || {});
                const dynamic = ['auto'];
                if (supported.includes('sgpt'))
                    dynamic.push('sgpt');
                if (supported.includes('codex'))
                    dynamic.push('codex');
                if (supported.includes('opencode'))
                    dynamic.push('opencode');
                if (supported.includes('aider'))
                    dynamic.push('aider');
                if (supported.includes('mistral_code'))
                    dynamic.push('mistral_code');
                this.availableCliBackends = dynamic;
                this.cliRuntime = (data?.runtime && typeof data.runtime === 'object') ? data.runtime : {};
                if (!this.availableCliBackends.includes(this.cliBackend)) {
                    this.cliBackend = 'auto';
                }
                this.cdr.detectChanges();
            },
            error: () => { }
        });
        this.agentApi.getConfig(hub.url).subscribe({
            next: cfg => {
                const value = String(cfg?.sgpt_execution_backend || '').toLowerCase();
                if ((value === 'auto' || value === 'sgpt' || value === 'codex' || value === 'opencode' || value === 'aider' || value === 'mistral_code') &&
                    this.availableCliBackends.includes(value)) {
                    this.cliBackend = value;
                    this.cdr.detectChanges();
                }
            },
            error: () => { }
        });
    }
    onCliBackendChange() {
        const hub = this.hub;
        if (!hub)
            return;
        this.agentApi.setConfig(hub.url, { sgpt_execution_backend: this.cliBackend }).subscribe({
            next: () => { },
            error: () => { }
        });
    }
    setCliBackend(backend) {
        this.cliBackend = backend;
        this.onCliBackendChange();
    }
    selectedCliRuntime() {
        const effective = this.cliBackend === 'auto' ? this.inferAutoCliBackend() : this.cliBackend;
        return effective ? this.cliRuntime?.[effective] : null;
    }
    inferAutoCliBackend() {
        const snapshot = this.runtimeContext?.configSnapshot || {};
        const configured = String(snapshot?.sgpt_execution_backend || '').trim().toLowerCase();
        if (configured && configured !== 'auto')
            return configured;
        return 'sgpt';
    }
    assessPlanRisk(toolCalls) {
        return this.domain.assessPlanRisk(toolCalls);
    }
    storePendingPlan(msg) {
        this.storage.persistPendingPlan(this.pendingPlanStorageKey, msg);
    }
    restorePendingPlan() {
        const restored = this.storage.restorePendingPlan(this.pendingPlanStorageKey);
        if (!restored)
            return;
        this.chatHistory.push({
            role: 'assistant',
            content: 'Restored pending action plan from last session.',
            requiresConfirmation: true,
            pendingPrompt: restored.pendingPrompt,
            toolCalls: restored.toolCalls,
            planRisk: this.assessPlanRisk(restored.toolCalls),
        });
        this.persistChatHistory();
    }
    persistDockState() {
        this.storage.persistBoolean(this.dockStateStorageKey, this.minimized);
    }
    restoreDockState() {
        this.minimized = this.storage.restoreBoolean(this.dockStateStorageKey, true);
    }
    clearPendingPlan() {
        this.storage.clear(this.pendingPlanStorageKey);
    }
    buildAssistantRequestContext() {
        return {
            route: this.runtimeContext.route,
            selected_agent: this.runtimeContext.selectedAgentName || null,
            user: {
                name: this.runtimeContext.userName || null,
                role: this.runtimeContext.userRole || null,
            },
            agents: this.runtimeContext.agents,
            teams_count: this.runtimeContext.teamsCount,
            templates_count: this.runtimeContext.templatesCount,
            templates_summary: this.runtimeContext.templatesSummary,
            settings_summary: this.runtimeContext.settingsSummary || null,
            editable_settings: this.runtimeContext.editableSettings,
            automation_summary: this.runtimeContext.automationSummary || null,
            has_config: this.runtimeContext.hasConfig,
            config_snapshot: this.runtimeContext.configSnapshot || null,
        };
    }
    quickActions() {
        return this.domain.quickActions(this.runtimeContext.route || '/');
    }
    runQuickAction(prompt) {
        if (this.busy)
            return;
        this.chatInput = prompt;
        this.sendChat();
    }
    toCompactConfigSnapshot(cfg) {
        if (!cfg || typeof cfg !== 'object')
            return null;
        return {
            default_provider: cfg.default_provider || null,
            default_model: cfg.default_model || null,
            template_agent_name: cfg.template_agent_name || null,
            team_agent_name: cfg.team_agent_name || null,
            sgpt_execution_backend: cfg.sgpt_execution_backend || null,
            llm_config: cfg.llm_config ? {
                provider: cfg.llm_config.provider || null,
                model: cfg.llm_config.model || null,
                lmstudio_api_mode: cfg.llm_config.lmstudio_api_mode || null,
            } : null,
            codex_cli: cfg.codex_cli ? {
                base_url: cfg.codex_cli.base_url || null,
                api_key_profile: cfg.codex_cli.api_key_profile || null,
                prefer_lmstudio: cfg.codex_cli.prefer_lmstudio ?? null,
            } : null,
        };
    }
    toTemplateSummary(templates) {
        if (!Array.isArray(templates))
            return [];
        const maxTemplates = 25;
        const maxDescriptionChars = 180;
        return templates
            .flatMap((tpl) => {
            const name = String(tpl?.name || '').trim();
            if (!name)
                return [];
            const rawDescription = String(tpl?.description || '').replace(/\s+/g, ' ').trim();
            const description = rawDescription ? rawDescription.slice(0, maxDescriptionChars) : undefined;
            return [description ? { name, description } : { name }];
        })
            .slice(0, maxTemplates);
    }
    toEditableSettingsSummary(items) {
        if (!Array.isArray(items))
            return [];
        return items
            .map((item) => ({
            key: String(item?.key || '').trim(),
            path: item?.path ? String(item.path) : undefined,
            type: item?.type ? String(item.type) : undefined,
            endpoint: item?.endpoint ? String(item.endpoint) : undefined,
        }))
            .filter((item) => !!item.key)
            .slice(0, 60);
    }
    toLegacySettingsSummary(cfg) {
        if (!cfg || typeof cfg !== 'object')
            return null;
        return {
            llm: {
                default_provider: cfg.default_provider || null,
                default_model: cfg.default_model || null,
            },
            system: {
                log_level: cfg.log_level || null,
                http_timeout: cfg.http_timeout ?? null,
                command_timeout: cfg.command_timeout ?? null,
            },
        };
    }
    persistChatHistory() {
        this.domain.persistHistory(this.historyStorageKey, this.chatHistory);
    }
    restoreChatHistory() {
        this.chatHistory = this.domain.restoreHistory(this.historyStorageKey);
    }
};
AiAssistantComponent = __decorate([
    Component({
        standalone: true,
        selector: 'app-ai-assistant',
        imports: [CommonModule, AiAssistantMessageListComponent, AiAssistantControlsComponent],
        template: `
    <div class="ai-assistant-container" data-testid="assistant-dock" [class.minimized]="minimized" [attr.data-state]="minimized ? 'minimized' : 'expanded'">
      <div class="header" data-testid="assistant-dock-header" (click)="toggleMinimize()">
        <span>AI Assistant</span>
        <div class="controls">
          <button (click)="toggleMinimize(); $event.stopPropagation()" class="control-btn">
            {{ minimized ? '^' : 'v' }}
          </button>
        </div>
      </div>
    
      @if (!minimized) {
        <div class="content">
          <app-ai-assistant-message-list
            [chatHistory]="chatHistory"
            [busy]="busy"
            (previewSource)="previewSource($event)"
            (copySourcePath)="copySourcePath($event)"
            (executeSgpt)="executeSgpt($event)"
            (confirmAction)="confirmAction($event)"
            (cancelAction)="cancelAction($event)">
          </app-ai-assistant-message-list>
          <app-ai-assistant-controls
            [busy]="busy"
            [chatInput]="chatInput"
            [useHybridContext]="useHybridContext"
            [cliBackend]="cliBackend"
            [availableCliBackends]="availableCliBackends"
            [selectedCliRuntime]="selectedCliRuntime()"
            [lastFailedRequest]="lastFailedRequest"
            [runtimeContext]="runtimeContext"
            [quickActions]="quickActions()"
            (chatInputChange)="chatInput = $event"
            (useHybridContextChange)="useHybridContext = $event"
            (send)="sendChat()"
            (retryLast)="retryLastFailed()"
            (refreshContext)="refreshRuntimeContext()"
            (quickAction)="runQuickAction($event)"
            (cliBackendChange)="setCliBackend($event)">
          </app-ai-assistant-controls>
        </div>
      }
    </div>
    
    <style>
      .ai-assistant-container {
      position: fixed;
      bottom: 0;
      right: 20px;
      width: 380px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px 8px 0 0;
      box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
      z-index: 1000;
      display: flex;
      flex-direction: column;
      transition: height 0.3s ease;
      color: var(--fg);
    }
    .ai-assistant-container.minimized {
    height: 40px;
    }
    .header {
    background: var(--accent);
    color: white;
    padding: 8px 15px;
    border-radius: 8px 8px 0 0;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-weight: bold;
    }
    .content {
    height: 450px;
    display: flex;
    flex-direction: column;
    padding: 10px;
    }
    .control-btn {
    background: none;
    border: none;
    color: white;
    cursor: pointer;
    font-size: 12px;
    }
    @media (max-width: 900px) {
      .ai-assistant-container {
        right: 0;
        left: 0;
        bottom: 0;
        width: auto;
      }
      .ai-assistant-container:not(.minimized) {
        height: 100dvh;
        border-radius: 0;
      }
      .ai-assistant-container:not(.minimized) .header {
        border-radius: 0;
      }
      .content {
        height: calc(100dvh - 56px);
      }
    }
    </style>
    `
    })
], AiAssistantComponent);
export { AiAssistantComponent };
//# sourceMappingURL=ai-assistant.component.js.map