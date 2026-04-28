import { Component, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Observable, finalize, filter, take } from 'rxjs';
import { Capacitor } from '@capacitor/core';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { TerminalComponent } from './terminal.component';
import { TerminalMode } from '../services/terminal.service';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { PythonRuntimeService } from '../services/python-runtime.service';

@Component({
  standalone: true,
  selector: 'app-agent-panel',
  imports: [FormsModule, TerminalComponent],
  styles: [`
    .tab-btn {
      padding: 8px 16px;
      border: none;
      background: none;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      font-weight: 500;
    }
    .tab-btn.active {
      border-bottom: 2px solid var(--primary-color, #007bff);
      color: var(--primary-color, #007bff);
    }
    .tab-btn:hover:not(.active) {
      background: #f0f0f0;
    }
  `],
  template: `
    <div class="row flex-between">
      <h2>Agent Panel – {{agent?.name}}</h2>
      @if (agent) {
        <a [href]="agent.url + '/apidocs'" target="_blank" class="button-outline btn-sm-docs">API Docs (Swagger)</a>
      }
    </div>
    <p class="muted">{{agent?.url}}</p>
    
    <div class="row tab-row">
      <button class="tab-btn" [class.active]="activeTab === 'interact'" (click)="setTab('interact')">Interaktion</button>
      <button class="tab-btn" [class.active]="activeTab === 'config'" (click)="setTab('config')">Konfiguration</button>
      <button class="tab-btn" [class.active]="activeTab === 'llm'" (click)="setTab('llm')">LLM</button>
      <button class="tab-btn" [class.active]="activeTab === 'logs'" (click)="setTab('logs')">Logs</button>
      <button class="tab-btn" [class.active]="activeTab === 'terminal'" (click)="setTab('terminal')">Terminal</button>
      <button class="tab-btn" [class.active]="activeTab === 'system'" (click)="setTab('system')">System</button>
    </div>
    
    @if (activeTab === 'interact') {
      <div class="card grid">
        <label>Prompt
          <textarea [(ngModel)]="prompt" rows="6" placeholder="REASON/COMMAND Format"></textarea>
        </label>
        <label>Command (manuell)
          <input [(ngModel)]="command" placeholder="z. B. echo hello" />
        </label>
        <div class="row">
          <button (click)="onPropose()" [disabled]="busy">Vorschlag holen</button>
          <button (click)="onExecute()" [disabled]="busy || !command">Ausführen</button>
          @if (busy) {
            <span class="muted">Bitte warten…</span>
          }
        </div>
        @if (reason || command) {
          <div class="grid">
            <div><strong>Reason:</strong> {{reason}}</div>
            <div><strong>Command:</strong> <code>{{command}}</code></div>
          </div>
        }
        @if (execOut) {
          <div class="card">
            <div><strong>Exit:</strong> {{execExit}}</div>
            <pre class="pre-wrap">{{execOut}}</pre>
          </div>
        }
      </div>
    }
    
    @if (activeTab === 'config') {
      <div class="card">
        <h3>Konfiguration</h3>
        <textarea [(ngModel)]="configJson" rows="12" class="font-mono-textarea"></textarea>
        <div class="row mt-8">
          <button (click)="saveConfig()" [disabled]="busy">Speichern</button>
          <button (click)="loadConfig()" class="button-outline" [disabled]="busy">Neu laden</button>
        </div>
      </div>
    }
    
    @if (activeTab === 'llm') {
      <div class="card grid">
        <h3>LLM Konfiguration</h3>
        <p class="muted">Diese Einstellungen werden direkt im Agenten gespeichert und für seine Aufgaben verwendet.</p>
        <div class="grid cols-2">
          <label>Provider
            <select [(ngModel)]="llmConfig.provider">
              <option value="ollama">Ollama</option>
              <option value="lmstudio">LMStudio</option>
              <option value="openai">OpenAI</option>
              <option value="codex">OpenAI Codex</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </label>
          <label>Model
            <input [(ngModel)]="llmConfig.model" placeholder="llama3, gpt-4o-mini, etc." />
          </label>
        </div>
        @if (llmConfig.provider === 'lmstudio') {
          <label>LM Studio Modus
            <select [(ngModel)]="llmConfig.lmstudio_api_mode">
              <option value="chat">chat/completions</option>
              <option value="completions">completions</option>
            </select>
          </label>
        }
        <label>Base URL (optional, überschreibt Default)
          <input [(ngModel)]="llmConfig.base_url" placeholder="z.B. http://localhost:11434/api/generate" />
        </label>
        <label>API Key / Secret (optional)
          <input [(ngModel)]="llmConfig.api_key" type="password" placeholder="Sk-..." />
        </label>
        <label>API Key Profil (optional)
          <input [(ngModel)]="llmConfig.api_key_profile" placeholder="z.B. codex-main" />
        </label>
        <div class="grid cols-2">
          <label>Temperature (0.0 - 2.0)
            <input [(ngModel)]="llmConfig.temperature" type="number" min="0" max="2" step="0.1" placeholder="0.2" />
            @if (!isTemperatureValid()) {
              <span class="error-text">Temperature muss zwischen 0.0 und 2.0 liegen.</span>
            }
          </label>
          <label>Context Limit (Tokens)
            <input [(ngModel)]="llmConfig.context_limit" type="number" min="256" step="1" placeholder="4096" />
            @if (!isContextLimitValid()) {
              <span class="error-text">Context Limit muss mindestens 256 sein.</span>
            }
          </label>
        </div>
        <div class="row mt-10">
          <button data-testid="agent-panel-llm-save" (click)="saveLLMConfig()" [disabled]="llmSaving || !isLlmConfigValid()">LLM Speichern</button>
        </div>
        <hr class="hr-20"/>
        <h3>LLM Test</h3>
        <label>Test Prompt
          <textarea [(ngModel)]="testPrompt" rows="3" placeholder="Schreibe einen kurzen Test-Satz."></textarea>
        </label>
        <div class="row">
          <button (click)="testLLM()" [disabled]="llmTesting || !testPrompt || !isLlmConfigValid()">Generieren</button>
          @if (llmTesting) {
            <span class="muted">KI denkt nach...</span>
          }
        </div>
        @if (testResult) {
          <div class="card card-light-blue mt-10">
            <strong>Resultat:</strong>
            <p class="result-text">{{testResult}}</p>
          </div>
        }
      </div>
    }
    
    @if (activeTab === 'system') {
      <div class="card">
        <h3>System & Status</h3>
        <div class="grid cols-2">
          <div>
            <h4>Aktionen</h4>
            <button (click)="onRotateToken()" class="danger" [disabled]="busy">Token rotieren</button>
            <p class="muted token-hint">Generiert einen neuen Agent-Token und ungültig macht den alten.</p>
          </div>
          <div>
            <h4>Metrics</h4>
            <button (click)="loadMetrics()" [disabled]="busy">Metrics laden</button>
          </div>
        </div>
        @if (metrics) {
          <div class="card card-light-gray mt-12">
            <pre class="pre-scroll">{{metrics}}</pre>
          </div>
        }
      </div>
    }
    
    @if (activeTab === 'logs') {
      <div class="card">
        <h3>Letzte Logs</h3>
        @if (logs.length) {
          <div class="grid">
            @for (l of logs; track l) {
              <div class="row log-row">
                <div class="log-command">
                  <code>{{l.command}}</code>
                </div>
                <span class="muted log-rc">RC: {{l.returncode}}</span>
              </div>
            }
          </div>
        } @else {
          <p class="muted">Keine Logs vorhanden.</p>
        }
      </div>
    }

    @if (activeTab === 'terminal' && agent) {
      <div class="card grid">
        <div class="row flex-between">
          <h3 class="h3-no-margin">Live Terminal</h3>
          <label class="row mode-label">
            Modus
            <select [(ngModel)]="terminalMode">
              <option value="interactive">interactive</option>
              <option value="read">read-only</option>
            </select>
          </label>
        </div>
        <label>Forward Param (optional)
          <input [(ngModel)]="terminalForwardParam" placeholder="z. B. cli-... fuer taskgebundene Live-Terminals" />
        </label>
        <app-terminal
          [baseUrl]="agent.url"
          [token]="getRequestToken()"
          [mode]="terminalMode"
          [forwardParam]="terminalForwardParam || undefined"
        ></app-terminal>

        @if (isAndroidNative && agent.role !== 'hub') {
          <div class="card card-light grid mt-10">
            <h4>Android Worker Shell</h4>
            <p class="muted">Fallback-Terminal direkt in der App (lokale Shell-Ausfuehrung).</p>
            <label>Shell-Befehl
              <input [(ngModel)]="workerShellCommand" placeholder="z. B. cd /data/data/com.termux/files/home/ananta && python -m agent.ai_agent" />
            </label>
            <div class="row">
              <button (click)="runWorkerShellCommand()" [disabled]="workerShellBusy">Ausfuehren</button>
              <button class="button-outline" (click)="setWorkerShellStatusCommand()" [disabled]="workerShellBusy">Status-Befehl</button>
              <button class="button-outline" (click)="setWorkerShellStartCommand()" [disabled]="workerShellBusy">Start-Befehl</button>
            </div>
            <div class="muted">{{ workerShellMeta || '-' }}</div>
            <pre class="pre-scroll">{{ workerShellOutput || 'Noch keine Ausgabe.' }}</pre>
          </div>
        }
      </div>
    }
    `
})
export class AgentPanelComponent {
  private route = inject(ActivatedRoute);
  private dir = inject(AgentDirectoryService);
  private api = inject(AgentApiService);
  private userAuth = inject(UserAuthService);
  private ns = inject(NotificationService);
  private taskFacade = inject(TaskManagementFacade);
  private pythonRuntime = inject(PythonRuntimeService);

  agent?: AgentEntry;
  activeTab = 'interact';
  prompt = '';
  reason = '';
  command = '';
  execOut = '';
  execExit: any = '';
  busy = false;
  llmSaving = false;
  llmTesting = false;
  logs: any[] = [];
  configJson = '';
  metrics = '';
  llmConfig: any = {
    provider: 'ollama',
    model: '',
    base_url: '',
    api_key: '',
    api_key_profile: '',
    lmstudio_api_mode: 'chat',
    temperature: 0.2,
    context_limit: 4096
  };
  testPrompt = '';
  testResult = '';
  terminalMode: TerminalMode = 'interactive';
  terminalForwardParam = '';
  private terminalForwardParamAutoResolved = false;
  workerShellCommand = '';
  workerShellOutput = '';
  workerShellMeta = '';
  workerShellBusy = false;

  constructor() {
    const name = this.route.snapshot.paramMap.get('name')!;
    this.agent = this.dir.get(name);
    if (!this.agent) return;
    const tabParam = this.route.snapshot.queryParamMap.get('tab');
    if (tabParam) this.activeTab = tabParam;
    const modeParam = this.route.snapshot.queryParamMap.get('mode');
    if (modeParam === 'read' || modeParam === 'interactive') {
      this.terminalMode = modeParam;
    }
    const forwardParam = (this.route.snapshot.queryParamMap.get('forward_param') || '').trim();
    if (forwardParam) {
      this.terminalForwardParam = forwardParam;
      this.terminalForwardParamAutoResolved = true;
    }
    this.loadLogs();
    this.ensureConfigLoaded();
    this.ensureTerminalForwardParamLoaded();
    if (this.isAndroidNative && this.agent?.role !== 'hub') {
      this.setWorkerShellStatusCommand();
    }
  }

  get isAndroidNative(): boolean {
    return this.pythonRuntime.isNative && Capacitor.getPlatform() === 'android';
  }

  setTab(t: string) {
    this.activeTab = t;
    if (t === 'terminal') {
      this.ensureTerminalForwardParamLoaded();
    }
  }

  getRequestToken(): string | undefined {
    if (!this.agent) return undefined;
    if (this.agent.role === 'hub') {
      return this.userAuth.token || this.agent.token;
    }
    return this.agent.token || this.userAuth.token;
  }

  onPropose() {
    if (!this.agent) return;
    this.busy = true;
    this.api.propose(this.agent.url, { prompt: this.prompt }, this.getRequestToken()).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r: any) => { this.reason = r?.reason || ''; this.command = r?.command || ''; },
      error: () => {
        this.ns.error('Fehler beim Abrufen des Vorschlags');
      }
    });
  }
  onExecute() {
    if (!this.agent || !this.command) return;
    this.busy = true;
    this.api.execute(this.agent.url, { command: this.command }, this.getRequestToken()).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r: any) => { 
        this.execOut = r?.output ?? r?.stdout ?? '';
        this.execExit = r?.exit_code ?? r?.exitCode ?? r?.returncode;
        if (this.execExit === 0) this.ns.success('Befehl erfolgreich ausgeführt');
        else this.ns.error(`Befehl fehlgeschlagen (Exit: ${this.execExit})`);
        this.loadLogs(); 
      },
      error: () => { 
        this.execOut = 'Fehler bei Ausführung'; 
        this.ns.error('Fehler bei der Kommunikation mit dem Agenten');
      }
    });
  }
  loadLogs() {
    if (!this.agent) return;
    this.api.logs(this.agent.url, 50, undefined, this.getRequestToken()).subscribe({ 
      next: (r: any) => this.logs = r || [],
      error: () => this.ns.error('Logs konnten nicht geladen werden')
    });
  }

  private ensureConfigLoaded() {
    if (this.userAuth.token) {
      this.loadConfig();
      return;
    }
    this.userAuth.token$.pipe(
      filter((token): token is string => !!token),
      take(1)
    ).subscribe(() => this.loadConfig());
  }

  loadConfig() {
    if (!this.agent) return;
    this.api.getConfig(this.agent.url, this.getRequestToken()).subscribe({
      next: (cfg) => {
        const safeCfg = cfg && typeof cfg === 'object' ? cfg : {};
        this.configJson = JSON.stringify(safeCfg, null, 2);
        if (!this.busy && safeCfg.llm_config) {
          this.llmConfig = { ...this.llmConfig, ...safeCfg.llm_config };
        }
      },
      error: () => this.ns.error('Konfiguration konnte nicht geladen werden')
    });
  }
  saveConfig() {
    if (!this.agent) return;
    try {
      const cfg = JSON.parse(this.configJson);
      this.busy = true;
      this.api.setConfig(this.agent.url, cfg, this.getRequestToken()).pipe(
        finalize(() => this.busy = false)
      ).subscribe({
        next: () => {
          this.ns.success('Konfiguration gespeichert');
          this.loadConfig();
        },
        error: () => this.ns.error('Konfiguration konnte nicht gespeichert werden')
      });
    } catch(e) {
      this.ns.error('Ungültiges JSON-Format');
    }
  }

  saveLLMConfig() {
    if (!this.agent) return;
    if (!this.isLlmConfigValid()) {
      this.ns.error('LLM Konfiguration ist ungueltig (Temperature/Context Limit).');
      return;
    }
    try {
      const currentCfg = this.configJson.trim() ? JSON.parse(this.configJson) : {};
      currentCfg.llm_config = this.llmConfig;
      this.llmSaving = true;
      this.api.setConfig(this.agent.url, currentCfg, this.getRequestToken()).pipe(
        finalize(() => this.llmSaving = false)
      ).subscribe({
        next: () => {
          this.ns.success('LLM Konfiguration gespeichert');
          this.configJson = JSON.stringify(currentCfg, null, 2);
        },
        error: () => this.ns.error('Fehler beim Speichern der LLM Konfiguration')
      });
    } catch (e) { this.ns.error('Fehler beim Aktualisieren der Konfiguration'); }
  }

  testLLM() {
    if (!this.agent) return;
    if (!this.isLlmConfigValid()) {
      this.ns.error('LLM Konfiguration ist ungueltig (Temperature/Context Limit).');
      return;
    }
    this.llmTesting = true;
    this.testResult = '';
    this.api.llmGenerate(this.agent.url, this.testPrompt, this.llmConfig, this.getRequestToken()).pipe(
      finalize(() => this.llmTesting = false)
    ).subscribe({
      next: r => this.testResult = r.response,
      error: e => this.ns.error('LLM Test fehlgeschlagen: ' + (e.error?.message || e.message))
    });
  }

  isTemperatureValid(): boolean {
    const t = Number(this.llmConfig?.temperature);
    if (!Number.isFinite(t)) return false;
    return t >= 0 && t <= 2;
  }

  isContextLimitValid(): boolean {
    const c = Number(this.llmConfig?.context_limit);
    if (!Number.isFinite(c)) return false;
    return c >= 256;
  }

  isLlmConfigValid(): boolean {
    return this.isTemperatureValid() && this.isContextLimitValid();
  }
  onRotateToken() {
    if (!this.agent || !this.agent.token) return;
    if (!confirm("Token wirklich rotieren? Der aktuelle Token wird sofort ungültig.")) return;
    this.busy = true;
    this.api.rotateToken(this.agent.url, this.getRequestToken()).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r) => {
        if (this.agent) {
          this.agent.token = r.new_token;
          this.dir.upsert(this.agent);
          this.ns.success('Token wurde erfolgreich rotiert');
        }
      },
      error: () => this.ns.error('Token-Rotation fehlgeschlagen')
    });
  }
  loadMetrics() {
    if (!this.agent) return;
    this.busy = true;
    this.api.getMetrics(this.agent.url).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (m) => this.metrics = m
    });
  }

  private ensureTerminalForwardParamLoaded(): void {
    if (this.terminalForwardParamAutoResolved || this.terminalForwardParam.trim() || !this.agent || this.agent.role === 'hub') {
      return;
    }
    const hub = this.dir.get('hub');
    if (!hub?.url) return;
    const token = this.userAuth.token || hub.token;
    this.terminalForwardParamAutoResolved = true;
    this.taskFacade.listTasks(hub.url, token).subscribe({
      next: (tasks: any[] | null | undefined) => {
        const match = this.pickPreferredLiveTerminalTask(Array.isArray(tasks) ? tasks : []);
        const forwardParam = this.extractTaskForwardParam(match);
        if (forwardParam) {
          this.terminalForwardParam = forwardParam;
        }
      },
      error: () => {
        this.terminalForwardParamAutoResolved = false;
      }
    });
  }

  private pickPreferredLiveTerminalTask(tasks: any[]): any | undefined {
    if (!this.agent) return undefined;
    const candidates = tasks
      .filter((task) => this.isTaskAssignedToAgent(task, this.agent!))
      .filter((task) => !!this.extractTaskForwardParam(task));
    if (candidates.length === 0) return undefined;
    return candidates.sort((a, b) => this.compareTaskPriority(a, b))[0];
  }

  private isTaskAssignedToAgent(task: any, agent: AgentEntry): boolean {
    const agentUrl = String(agent.url || '').trim();
    if (!agentUrl) return false;
    const liveTerminalAgentUrl = String(
      task?.last_proposal?.routing?.live_terminal?.agent_url
      || task?.verification_status?.opencode_live_terminal?.agent_url
      || task?.verification_status?.cli_session?.agent_url
      || ''
    ).trim();
    const directAssigned = String(task?.assigned_agent_url || '').trim();
    const delegatedAssigned = String(task?.agent_url || '').trim();
    return liveTerminalAgentUrl === agentUrl || directAssigned === agentUrl || delegatedAssigned === agentUrl;
  }

  private extractTaskForwardParam(task: any): string {
    return String(
      task?.last_proposal?.routing?.live_terminal?.forward_param
      || task?.verification_status?.opencode_live_terminal?.forward_param
      || task?.verification_status?.cli_session?.forward_param
      || ''
    ).trim();
  }

  private compareTaskPriority(a: any, b: any): number {
    const byStatus = this.taskActivityRank(b) - this.taskActivityRank(a);
    if (byStatus !== 0) return byStatus;
    const byWorkerJob = Number(!!b?.current_worker_job_id) - Number(!!a?.current_worker_job_id);
    if (byWorkerJob !== 0) return byWorkerJob;
    const byUpdated = this.taskTimestamp(b) - this.taskTimestamp(a);
    if (byUpdated !== 0) return byUpdated;
    return String(b?.id || '').localeCompare(String(a?.id || ''));
  }

  private taskActivityRank(task: any): number {
    const status = String(task?.status || '').trim().toLowerCase();
    if (status === 'in_progress') return 4;
    if (status === 'proposing') return 3;
    if (status === 'assigned') return 2;
    if (status === 'todo' || status === 'created') return 1;
    return 0;
  }

  private taskTimestamp(task: any): number {
    const candidates = [
      task?.updated_at,
      task?.verification_status?.updated_at,
      task?.last_proposal?.routing?.live_terminal?.updated_at,
      task?.verification_status?.opencode_live_terminal?.updated_at,
      task?.created_at
    ];
    for (const candidate of candidates) {
      const value = Number(candidate);
      if (Number.isFinite(value) && value > 0) return value;
    }
    return 0;
  }

  setWorkerShellStatusCommand(): void {
    this.workerShellCommand = [
      'cd /data/data/com.termux/files/home/ananta',
      'echo "== worker process =="',
      'ps -ef | grep "python -m agent.ai_agent" | grep -v grep || true',
      'echo "== worker health =="',
      'curl -sf http://127.0.0.1:5001/health || true',
    ].join(' && ');
  }

  setWorkerShellStartCommand(): void {
    this.workerShellCommand = [
      'cd /data/data/com.termux/files/home/ananta',
      'ROLE=worker AGENT_NAME=android-worker PORT=5001 HUB_URL=http://127.0.0.1:5000 AGENT_URL=http://127.0.0.1:5001 python -m agent.ai_agent',
    ].join(' && ');
  }

  runWorkerShellCommand(): void {
    if (!this.isAndroidNative || this.workerShellBusy) return;
    const command = (this.workerShellCommand || '').trim();
    if (!command) {
      this.ns.error('Bitte zuerst einen Shell-Befehl eingeben.');
      return;
    }
    this.workerShellBusy = true;
    this.workerShellMeta = 'Laeuft...';
    this.pythonRuntime.runShellCommand(command, 120).then(
      (result) => {
        this.workerShellOutput = result.output || '';
        this.workerShellMeta = `Exit-Code: ${result.exitCode}${result.timedOut ? ' | Timeout' : ''}`;
      },
      (error: any) => {
        const message = error?.message || String(error);
        this.workerShellOutput = message;
        this.workerShellMeta = 'Fehler';
      }
    ).finally(() => {
      this.workerShellBusy = false;
    });
  }
}
