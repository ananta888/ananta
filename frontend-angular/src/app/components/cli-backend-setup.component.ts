import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';

import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { SystemFacade } from '../features/system/system.facade';
import { CliBackendAccountLoginComponent } from './cli-backend-account-login.component';

interface BackendCardState {
  loading: boolean;
  status: 'ready' | 'not_installed' | 'disabled' | 'error' | 'unknown';
  provider: any;
  preflight: any;
  diagnose: any | null;
  diagnosing: boolean;
  testRunning: boolean;
  testResult: { ok: boolean; stdout: string; stderr: string; duration_ms: number } | null;
}

interface WorkerTarget {
  name: string;
  url: string;
  status: string;
}

function emptyCard(): BackendCardState {
  return { loading: false, status: 'unknown', provider: null, preflight: null, diagnose: null, diagnosing: false, testRunning: false, testResult: null };
}

/**
 * CCA-003 + CLA-003: Setup-/Diagnose-Karten fuer die
 * Subscription-CLI-Backends (OpenAI Codex CLI, Claude Code CLI).
 *
 * Sicherheitskontrakt: Der Hub routet ausschließlich kurzlebige
 * Login-Sitzungen. Credential-Dateien bleiben im persistenten Worker-Home.
 */
@Component({
  standalone: true,
  selector: 'app-cli-backend-setup',
  imports: [FormsModule, CliBackendAccountLoginComponent],
  template: `
    <div class="card">
      <div class="row flex-between">
        <h3>CLI-Backends (Codex &amp; Claude)</h3>
        <button class="button-outline" (click)="reload()">🔄 Aktualisieren</button>
      </div>
      <p class="muted">
        Worker-Agent-CLIs mit Account-Login oder API-Key. Der Browser-Login wird hier gestartet;
        OAuth-Tokens bleiben ausschließlich im persistenten Home des ausgewählten Workers.
      </p>
      <p class="muted">
        Installationen werden vom Hub freigegeben und versioniert im persistenten Home des
        ausgewählten Workers abgelegt.
      </p>

      <div class="cards">
        <!-- Codex Card (CCA-003) -->
        <div class="backend-card" data-testid="codex-card">
          <div class="row flex-between">
            <strong>OpenAI Codex CLI</strong>
            <span class="status-chip" [class]="'chip-' + executionStatus('codex')">{{ statusLabel(executionStatus('codex')) }}</span>
          </div>
          @if (codex.preflight?.install_hint && executionStatus('codex') === 'not_installed') {
            <p class="hint">Installieren: <code>{{ codex.preflight.install_hint }}</code></p>
          }
          <div class="worker-picker" data-testid="codex-worker-picker">
            <span class="picker-title">Auf Workern bereitstellen</span>
            @for (worker of workers; track worker.url) {
              <label class="worker-row">
                <input
                  type="checkbox"
                  [disabled]="worker.status !== 'online' || isProvisioning('codex', worker)"
                  [ngModel]="isWorkerSelected('codex', worker)"
                  (ngModelChange)="setWorkerSelected('codex', worker, $event)"
                />
                <span>{{ worker.name }}</span>
                <small>{{ provisioningLabel('codex', worker) }}</small>
              </label>
            } @empty {
              <span class="hint">Keine registrierten Worker gefunden.</span>
            }
            <button
              class="button-outline"
              [disabled]="!hasSelectedWorkers('codex') || isBackendProvisioning('codex')"
              (click)="installOnSelectedWorkers('codex')"
            >⬇ Auf Auswahl installieren</button>
          </div>
          <label class="label-block">
            Ausführungs-Worker
            <select [(ngModel)]="codexWorkerName" (ngModelChange)="executionWorkerChanged(codex)">
              @for (worker of workers; track worker.url) {
                <option [value]="worker.name">{{ worker.name }}</option>
              }
            </select>
          </label>
          <label class="label-block">
            Auth-Modus
            <select [(ngModel)]="codexAuthMode">
              <option value="api_key">API-Key</option>
              <option value="chatgpt_login">ChatGPT Account-Login</option>
            </select>
          </label>
          @if (codexAuthMode === 'api_key') {
            <label class="label-block">
              API-Key-Profil (kein Klartext-Key)
              <input [(ngModel)]="codexApiKeyProfile" placeholder="z.B. codex-prod" />
            </label>
          } @else {
            <app-cli-backend-account-login
              backendId="codex"
              [workerName]="codexWorkerName"
              [ready]="executionStatus('codex') === 'ready'"
            />
          }
          <div class="row btn-row">
            <button class="button-outline" (click)="saveCodex()">💾 Speichern</button>
            <button class="button-outline" [disabled]="codex.diagnosing || !codexWorkerName" (click)="diagnose('codex', codex)">
              {{ codex.diagnosing ? '⏳' : '🩺' }} Diagnose
            </button>
            <button class="button-outline" [disabled]="codex.testRunning || executionStatus('codex') !== 'ready'" (click)="testRun('codex', codex)">
              {{ codex.testRunning ? '⏳' : '▶' }} Test-Run (read-only)
            </button>
          </div>
          @if (codex.diagnose) {
            <pre class="probe-out">{{ probeSummary(codex.diagnose) }}</pre>
          }
          @if (codex.testResult) {
            <pre class="probe-out" [class.probe-err]="!codex.testResult.ok">{{ testSummary(codex.testResult) }}</pre>
          }
        </div>

        <!-- Claude Card (CLA-003) -->
        <div class="backend-card" data-testid="claude-card">
          <div class="row flex-between">
            <strong>Claude Code CLI</strong>
            <span class="status-chip" [class]="'chip-' + executionStatus('claude_code')">{{ statusLabel(executionStatus('claude_code')) }}</span>
          </div>
          @if (claude.provider?.install_hint && executionStatus('claude_code') === 'not_installed') {
            <p class="hint">Installieren: <code>{{ claude.provider.install_hint }}</code></p>
          }
          <div class="worker-picker" data-testid="claude-worker-picker">
            <span class="picker-title">Auf Workern bereitstellen</span>
            @for (worker of workers; track worker.url) {
              <label class="worker-row">
                <input
                  type="checkbox"
                  [disabled]="worker.status !== 'online' || isProvisioning('claude_code', worker)"
                  [ngModel]="isWorkerSelected('claude_code', worker)"
                  (ngModelChange)="setWorkerSelected('claude_code', worker, $event)"
                />
                <span>{{ worker.name }}</span>
                <small>{{ provisioningLabel('claude_code', worker) }}</small>
              </label>
            } @empty {
              <span class="hint">Keine registrierten Worker gefunden.</span>
            }
            <button
              class="button-outline"
              [disabled]="!hasSelectedWorkers('claude_code') || isBackendProvisioning('claude_code')"
              (click)="installOnSelectedWorkers('claude_code')"
            >⬇ Auf Auswahl installieren</button>
          </div>
          <label class="label-block">
            Ausführungs-Worker
            <select [(ngModel)]="claudeWorkerName" (ngModelChange)="executionWorkerChanged(claude)">
              @for (worker of workers; track worker.url) {
                <option [value]="worker.name">{{ worker.name }}</option>
              }
            </select>
          </label>
          <label class="check-block">
            <input type="checkbox" [(ngModel)]="claudeEnabled" /> Aktiviert (opt-in)
          </label>
          <label class="label-block">
            Auth-Modus
            <select [(ngModel)]="claudeAuthMode">
              <option value="claude_login">Claude Account-Login</option>
              <option value="api_key">API-Key (ANTHROPIC_API_KEY via Env)</option>
            </select>
          </label>
          @if (claudeAuthMode === 'claude_login') {
            <app-cli-backend-account-login
              backendId="claude_code"
              [workerName]="claudeWorkerName"
              [ready]="executionStatus('claude_code') === 'ready'"
              [claudeEnabled]="claudeEnabled"
              [claudePermissionMode]="claudePermissionMode"
              [claudeTimeoutSeconds]="claudeTimeoutSeconds"
            />
          }
          <label class="label-block">
            Permission-Modus
            <select [(ngModel)]="claudePermissionMode">
              <option value="plan">plan (read-only Analyse)</option>
              <option value="default">default</option>
            </select>
          </label>
          <label class="label-block">
            Default-Modell (leer = CLI-Default)
            <input [(ngModel)]="claudeDefaultModel" placeholder="claude-code-default" />
          </label>
          <label class="label-block">
            Timeout (Sekunden)
            <input type="number" [(ngModel)]="claudeTimeoutSeconds" min="30" max="14400" />
          </label>
          <div class="row btn-row">
            <button class="button-outline" (click)="saveClaude()">💾 Speichern</button>
            <button class="button-outline" [disabled]="claude.diagnosing || !claudeWorkerName" (click)="diagnose('claude_code', claude)">
              {{ claude.diagnosing ? '⏳' : '🩺' }} Diagnose
            </button>
            <button class="button-outline" [disabled]="claude.testRunning || !claudeEnabled || executionStatus('claude_code') !== 'ready'" (click)="testRun('claude_code', claude)">
              {{ claude.testRunning ? '⏳' : '▶' }} Test-Run (read-only)
            </button>
          </div>
          @if (claude.diagnose) {
            <pre class="probe-out">{{ probeSummary(claude.diagnose) }}</pre>
          }
          @if (claude.testResult) {
            <pre class="probe-out" [class.probe-err]="!claude.testResult.ok">{{ testSummary(claude.testResult) }}</pre>
          }
        </div>
      </div>
    </div>
  `,
  styles: [`
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; margin-top: 10px; }
    .backend-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
    .status-chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border); }
    .chip-ready { background: #1d643b22; color: #2fa96b; border-color: #2fa96b; }
    .chip-not_installed, .chip-error { background: #dc354522; color: #dc3545; border-color: #dc3545; }
    .chip-disabled, .chip-unknown { color: var(--fg); opacity: 0.7; }
    .hint { font-size: 12px; opacity: 0.85; margin: 0; }
    .label-block { display: block; font-size: 12px; }
    .check-block { display: block; font-size: 13px; }
    .btn-row { gap: 6px; flex-wrap: wrap; }
    .probe-out { font-size: 11px; max-height: 160px; overflow: auto; background: var(--bg-subtle, rgba(127,127,127,0.08)); padding: 6px; border-radius: 6px; white-space: pre-wrap; }
    .probe-err { border: 1px solid #dc3545; }
    .worker-picker { display: grid; gap: 6px; padding: 8px; border: 1px solid var(--border); border-radius: 6px; }
    .picker-title { font-size: 12px; font-weight: 600; }
    .worker-row { display: grid; grid-template-columns: auto minmax(80px, 1fr) auto; gap: 7px; align-items: center; font-size: 12px; }
    .worker-row small { opacity: 0.72; text-align: right; }
  `]
})
export class CliBackendSetupComponent implements OnInit {
  private system = inject(SystemFacade);
  private agentApi = inject(AgentApiService);
  private ns = inject(NotificationService);
  private cdr = inject(ChangeDetectorRef);

  codex: BackendCardState = emptyCard();
  claude: BackendCardState = emptyCard();
  workers: WorkerTarget[] = [];
  selectedWorkers: Record<string, boolean> = {};
  provisioningState: Record<string, 'loading' | 'ready' | 'not_installed' | 'error'> = {};
  provisioningDetails: Record<string, any> = {};
  codexWorkerName = '';
  claudeWorkerName = '';

  codexAuthMode: 'api_key' | 'chatgpt_login' = 'api_key';
  codexApiKeyProfile = '';

  claudeEnabled = false;
  claudeAuthMode: 'claude_login' | 'api_key' = 'claude_login';
  claudePermissionMode: 'plan' | 'default' = 'plan';
  claudeDefaultModel = '';
  claudeTimeoutSeconds = 1800;

  ngOnInit() {
    this.reload();
  }

  private hubUrl(): string | null {
    const hub = this.system.resolveHubAgent();
    if (!hub) {
      this.ns.error('Kein Hub-Agent gefunden');
      return null;
    }
    return hub.url;
  }

  reload() {
    const url = this.hubUrl();
    if (!url) return;
    this.loadHealth(url, 'codex', this.codex);
    this.loadHealth(url, 'claude_code', this.claude);
    this.loadWorkers(url);
    this.agentApi.getConfig(url).subscribe({
      next: (cfg: any) => {
        const codexCli = cfg?.codex_cli || {};
        this.codexAuthMode = codexCli.auth_mode === 'chatgpt_login' ? 'chatgpt_login' : 'api_key';
        this.codexApiKeyProfile = codexCli.api_key_profile || '';
        const claudeCli = cfg?.claude_cli || {};
        this.claudeEnabled = !!claudeCli.enabled;
        this.claudeAuthMode = claudeCli.auth_mode === 'api_key' ? 'api_key' : 'claude_login';
        this.claudePermissionMode = claudeCli.permission_mode === 'default' ? 'default' : 'plan';
        this.claudeDefaultModel = claudeCli.default_model || '';
        this.claudeTimeoutSeconds = Number(claudeCli.timeout_seconds) || 1800;
        this.cdr.detectChanges();
      },
      error: () => {},
    });
  }

  private loadWorkers(url: string) {
    this.agentApi.listAgents(url).subscribe({
      next: (items: any) => {
        const values = Array.isArray(items) ? items : Object.values(items || {});
        this.workers = values
          .filter((item: any) => String(item?.role || '').toLowerCase() === 'worker' && !!item?.url)
          .map((item: any) => ({
            name: String(item.name || item.url),
            url: String(item.url),
            status: String(item.status || 'unknown').toLowerCase(),
          }));
        this.codexWorkerName ||= this.workers[0]?.name || '';
        this.claudeWorkerName ||= this.workers[0]?.name || '';
        for (const worker of this.workers) {
          if (worker.status === 'online') {
            this.loadProvisioningStatus(url, 'codex', worker);
            this.loadProvisioningStatus(url, 'claude_code', worker);
          }
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.workers = [];
        this.cdr.detectChanges();
      },
    });
  }

  private loadProvisioningStatus(url: string, backendId: string, worker: WorkerTarget) {
    const key = this.selectionKey(backendId, worker);
    this.provisioningState[key] = 'loading';
    this.agentApi.sgptBackendProvision(url, backendId, { worker_url: worker.url, action: 'status' }).subscribe({
      next: (result: any) => {
        this.provisioningState[key] = result?.installed ? 'ready' : 'not_installed';
        this.provisioningDetails[key] = result;
        if (result?.installed && this.executionStatus(backendId) !== 'ready') {
          this.setExecutionWorkerName(backendId, worker.name);
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.provisioningState[key] = 'error';
        this.cdr.detectChanges();
      },
    });
  }

  installOnSelectedWorkers(backendId: 'codex' | 'claude_code') {
    const url = this.hubUrl();
    if (!url) return;
    const selected = this.workers.filter((worker) => this.isWorkerSelected(backendId, worker));
    for (const worker of selected) {
      const key = this.selectionKey(backendId, worker);
      this.provisioningState[key] = 'loading';
      this.agentApi.sgptBackendProvision(url, backendId, { worker_url: worker.url, action: 'install' }).subscribe({
        next: (result: any) => {
          this.provisioningState[key] = result?.installed ? 'ready' : 'error';
          this.provisioningDetails[key] = result;
          this.selectedWorkers[key] = false;
          this.ns.success(`${backendId === 'codex' ? 'Codex' : 'Claude'} auf ${worker.name} installiert`);
          this.cdr.detectChanges();
        },
        error: (error: any) => {
          this.provisioningState[key] = 'error';
          this.ns.error(`Installation auf ${worker.name} fehlgeschlagen: ${error?.message || error}`);
          this.cdr.detectChanges();
        },
      });
    }
  }

  setWorkerSelected(backendId: string, worker: WorkerTarget, selected: boolean) {
    this.selectedWorkers[this.selectionKey(backendId, worker)] = !!selected;
  }

  isWorkerSelected(backendId: string, worker: WorkerTarget): boolean {
    return !!this.selectedWorkers[this.selectionKey(backendId, worker)];
  }

  hasSelectedWorkers(backendId: string): boolean {
    return this.workers.some((worker) => this.isWorkerSelected(backendId, worker));
  }

  isProvisioning(backendId: string, worker: WorkerTarget): boolean {
    return this.provisioningState[this.selectionKey(backendId, worker)] === 'loading';
  }

  isBackendProvisioning(backendId: string): boolean {
    return this.workers.some((worker) => this.isProvisioning(backendId, worker));
  }

  provisioningLabel(backendId: string, worker: WorkerTarget): string {
    if (worker.status !== 'online') return 'offline';
    const state = this.provisioningState[this.selectionKey(backendId, worker)];
    if (state === 'loading') return 'wird geprüft …';
    if (state === 'ready') return 'installiert';
    if (state === 'not_installed') return 'nicht installiert';
    if (state === 'error') return 'nicht erreichbar';
    return 'unbekannt';
  }

  executionStatus(backendId: string): BackendCardState['status'] {
    const workerName = this.executionWorkerName(backendId);
    const worker = this.workers.find((item) => item.name === workerName);
    if (!worker) return 'unknown';
    const state = this.provisioningState[this.selectionKey(backendId, worker)];
    return state === 'loading' ? 'unknown' : state || 'unknown';
  }

  executionWorkerChanged(card: BackendCardState) {
    card.diagnose = null;
    card.testResult = null;
  }

  private executionWorkerName(backendId: string): string {
    return backendId === 'codex' ? this.codexWorkerName : this.claudeWorkerName;
  }

  private setExecutionWorkerName(backendId: string, workerName: string) {
    if (backendId === 'codex') this.codexWorkerName = workerName;
    else this.claudeWorkerName = workerName;
  }

  private selectionKey(backendId: string, worker: WorkerTarget): string {
    return `${backendId}|${worker.url}`;
  }

  private loadHealth(url: string, backendId: string, card: BackendCardState) {
    card.loading = true;
    this.agentApi.sgptBackendHealth(url, backendId).subscribe({
      next: (data: any) => {
        card.loading = false;
        card.status = (data?.status as BackendCardState['status']) || 'unknown';
        card.provider = data?.provider || null;
        card.preflight = data?.preflight || null;
        this.cdr.detectChanges();
      },
      error: () => {
        card.loading = false;
        card.status = 'error';
        this.cdr.detectChanges();
      },
    });
  }

  saveCodex() {
    const url = this.hubUrl();
    if (!url) return;
    const codexCli: any = { auth_mode: this.codexAuthMode };
    if (this.codexAuthMode === 'api_key' && this.codexApiKeyProfile.trim()) {
      codexCli.api_key_profile = this.codexApiKeyProfile.trim();
    }
    this.agentApi.setConfig(url, { codex_cli: codexCli }).subscribe({
      next: () => { this.ns.success('Codex-Konfiguration gespeichert'); this.reload(); },
      error: (e: any) => this.ns.error('Speichern fehlgeschlagen: ' + (e?.message || e)),
    });
  }


  saveClaude() {
    const url = this.hubUrl();
    if (!url) return;
    const claudeCli: any = {
      enabled: this.claudeEnabled,
      auth_mode: this.claudeAuthMode,
      permission_mode: this.claudePermissionMode,
      timeout_seconds: this.claudeTimeoutSeconds,
    };
    if (this.claudeDefaultModel.trim()) {
      claudeCli.default_model = this.claudeDefaultModel.trim();
    }
    this.agentApi.setConfig(url, { claude_cli: claudeCli }).subscribe({
      next: () => { this.ns.success('Claude-Konfiguration gespeichert'); this.reload(); },
      error: (e: any) => this.ns.error('Speichern fehlgeschlagen: ' + (e?.message || e)),
    });
  }

  diagnose(backendId: string, card: BackendCardState) {
    const url = this.hubUrl();
    const workerName = this.executionWorkerName(backendId);
    if (!url || !workerName) return;
    card.diagnosing = true;
    this.agentApi.sgptBackendWorkerAction(url, backendId, {
      worker_name: workerName,
      action: 'diagnose',
    }).subscribe({
      next: (data: any) => {
        card.diagnosing = false;
        card.diagnose = data;
        this.cdr.detectChanges();
      },
      error: (e: any) => {
        card.diagnosing = false;
        this.ns.error('Diagnose fehlgeschlagen: ' + (e?.message || e));
        this.cdr.detectChanges();
      },
    });
  }

  testRun(backendId: string, card: BackendCardState) {
    const url = this.hubUrl();
    const workerName = this.executionWorkerName(backendId);
    if (!url || !workerName) return;
    card.testRunning = true;
    card.testResult = null;
    this.agentApi.sgptBackendWorkerAction(url, backendId, {
      worker_name: workerName,
      action: 'test_run',
      prompt: 'Antworte nur mit dem Wort: OK',
      timeout: 120,
    }).subscribe({
      next: (data: any) => {
        card.testRunning = false;
        card.testResult = {
          ok: !!data?.ok,
          stdout: data?.stdout || '',
          stderr: data?.stderr || '',
          duration_ms: Number(data?.duration_ms) || 0,
        };
        this.cdr.detectChanges();
      },
      error: (e: any) => {
        card.testRunning = false;
        card.testResult = { ok: false, stdout: '', stderr: String(e?.message || e), duration_ms: 0 };
        this.cdr.detectChanges();
      },
    });
  }

  statusLabel(status: BackendCardState['status']): string {
    if (status === 'ready') return 'bereit';
    if (status === 'not_installed') return 'nicht installiert';
    if (status === 'disabled') return 'deaktiviert';
    if (status === 'error') return 'Fehler';
    return 'unbekannt';
  }

  probeSummary(diag: any): string {
    const probe = diag?.version_probe;
    const lines = [
      `Worker: ${diag?.worker?.name || '-'}`,
      `Status: ${diag?.status}`,
      `Binary: ${diag?.binary_path || '-'}`,
    ];
    if (probe) lines.push(`Version-Probe rc=${probe.rc}: ${(probe.stdout || probe.stderr || '').trim()}`);
    if (!diag?.binary_available && diag?.install_hint) lines.push(`Install: ${diag.install_hint}`);
    return lines.join('\n');
  }

  testSummary(result: { ok: boolean; stdout: string; stderr: string; duration_ms: number }): string {
    const head = result.ok ? `OK (${result.duration_ms} ms)` : `FEHLER (${result.duration_ms} ms)`;
    const body = (result.stdout || result.stderr || '').trim();
    return `${head}\n${body}`;
  }
}
