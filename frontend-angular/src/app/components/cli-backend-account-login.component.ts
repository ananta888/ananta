import {
  ChangeDetectorRef,
  Component,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { SystemFacade } from '../features/system/system.facade';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';

type AccountBackend = 'codex' | 'claude_code';

interface AccountLoginState {
  checking: boolean;
  authenticated: boolean | null;
  running: boolean;
  sessionId: string;
  status: string;
  verificationUrl: string;
  userCode: string;
  requiresInput: boolean;
  input: string;
  error: string;
}

function emptyState(): AccountLoginState {
  return {
    checking: false,
    authenticated: null,
    running: false,
    sessionId: '',
    status: 'unknown',
    verificationUrl: '',
    userCode: '',
    requiresInput: false,
    input: '',
    error: '',
  };
}

@Component({
  standalone: true,
  selector: 'app-cli-backend-account-login',
  imports: [FormsModule],
  template: `
    <div class="account-login" [attr.data-testid]="backendId + '-account-login'">
      <div class="row flex-between">
        <span>{{ statusLabel() }}</span>
        <button
          class="button-outline"
          [disabled]="state.running || !ready || !workerName"
          (click)="start()"
        >🔐 Mit {{ providerName }} anmelden</button>
      </div>
      @if (state.verificationUrl) {
        <a
          class="login-link"
          [href]="state.verificationUrl"
          target="_blank"
          rel="noopener noreferrer"
        >{{ providerName }}-Anmeldung im Browser öffnen</a>
      }
      @if (state.userCode) {
        <p class="device-code">Einmalcode: <code>{{ state.userCode }}</code></p>
      }
      @if (state.requiresInput) {
        <label class="label-block">
          Rückgabecode aus dem Browser
          <input
            [(ngModel)]="state.input"
            autocomplete="off"
            placeholder="Code hier einfügen"
          />
        </label>
        <button
          class="button-outline"
          [disabled]="!state.input.trim()"
          (click)="submitInput()"
        >Code bestätigen</button>
      }
      @if (state.running) {
        <button class="button-link" (click)="cancel()">Anmeldung abbrechen</button>
      }
      @if (state.error) {
        <p class="login-error">{{ state.error }}</p>
      }
    </div>
  `,
  styles: [`
    .account-login { display: grid; gap: 7px; padding: 9px; border: 1px solid var(--border); border-radius: 6px; }
    .login-link { font-size: 13px; overflow-wrap: anywhere; }
    .device-code { margin: 0; font-size: 13px; }
    .device-code code { font-size: 16px; letter-spacing: 0.08em; }
    .login-error { color: #dc3545; font-size: 12px; margin: 0; }
    .label-block { display: block; font-size: 12px; }
    .button-link { border: 0; background: transparent; color: var(--fg); text-decoration: underline; cursor: pointer; justify-self: start; padding: 0; }
  `],
})
export class CliBackendAccountLoginComponent implements OnChanges, OnDestroy {
  private system = inject(SystemFacade);
  private agentApi = inject(AgentApiService);
  private notifications = inject(NotificationService);
  private cdr = inject(ChangeDetectorRef);
  private pollTimer: ReturnType<typeof setTimeout> | undefined;

  @Input({ required: true }) backendId: AccountBackend = 'codex';
  @Input() workerName = '';
  @Input() ready = false;
  @Input() claudeEnabled = false;
  @Input() claudePermissionMode: 'plan' | 'manual' | 'acceptEdits' | 'dontAsk' | 'auto' = 'plan';
  @Input() claudeTimeoutSeconds = 1800;

  state = emptyState();

  get providerName(): string {
    return this.backendId === 'codex' ? 'ChatGPT' : 'Claude';
  }

  ngOnChanges(changes: SimpleChanges) {
    if (!changes['workerName'] && !changes['ready']) return;
    this.reset();
    if (this.ready && this.workerName) this.refreshStatus();
  }

  ngOnDestroy() {
    this.clearPoll();
  }

  start() {
    const baseUrl = this.hubUrl();
    if (!baseUrl || !this.workerName || !this.ready) return;
    this.clearPoll();
    this.state = {
      ...emptyState(),
      running: true,
      status: 'starting',
    };

    this.agentApi.setConfig(baseUrl, this.authConfig()).subscribe({
      next: () => {
        this.agentApi.sgptBackendWorkerAction(baseUrl, this.backendId, {
          worker_name: this.workerName,
          action: 'login_start',
        }).subscribe({
          next: (result: any) => this.applyResult(result),
          error: (error: any) => this.fail(error),
        });
      },
      error: (error: any) => this.fail(error),
    });
  }

  submitInput() {
    const baseUrl = this.hubUrl();
    const value = this.state.input.trim();
    if (!baseUrl || !this.workerName || !this.state.sessionId || !value) return;
    this.state.input = '';
    this.agentApi.sgptBackendWorkerAction(baseUrl, this.backendId, {
      worker_name: this.workerName,
      action: 'login_input',
      session_id: this.state.sessionId,
      value,
    }).subscribe({
      next: (result: any) => this.applyResult(result),
      error: (error: any) => this.fail(error),
    });
  }

  cancel() {
    const baseUrl = this.hubUrl();
    if (!baseUrl || !this.workerName || !this.state.sessionId) return;
    this.clearPoll();
    this.agentApi.sgptBackendWorkerAction(baseUrl, this.backendId, {
      worker_name: this.workerName,
      action: 'login_cancel',
      session_id: this.state.sessionId,
    }).subscribe({
      next: (result: any) => this.applyResult(result),
      error: (error: any) => this.fail(error),
    });
  }

  statusLabel(): string {
    if (this.state.checking) return 'Anmeldestatus wird geprüft …';
    if (this.state.authenticated) return 'Account ist auf diesem Worker angemeldet.';
    if (this.state.status === 'pending') return 'Anmeldung wartet auf Browser-Bestätigung.';
    if (this.state.status === 'expired') return 'Anmeldung ist abgelaufen.';
    if (this.state.status === 'cancelled') return 'Anmeldung wurde abgebrochen.';
    if (this.state.status === 'failed') return 'Anmeldung ist fehlgeschlagen.';
    return 'Noch kein Account auf diesem Worker angemeldet.';
  }

  private refreshStatus() {
    const baseUrl = this.hubUrl();
    if (!baseUrl || !this.workerName) return;
    this.state.checking = true;
    this.agentApi.sgptBackendWorkerAction(baseUrl, this.backendId, {
      worker_name: this.workerName,
      action: 'account_status',
    }).subscribe({
      next: (result: any) => {
        this.state.checking = false;
        this.state.authenticated = !!result?.authenticated;
        this.state.status = String(result?.status || 'unknown');
        this.cdr.detectChanges();
      },
      error: () => {
        this.state.checking = false;
        this.state.authenticated = null;
        this.cdr.detectChanges();
      },
    });
  }

  private applyResult(result: any) {
    this.state.sessionId = String(result?.session_id || this.state.sessionId || '');
    this.state.status = String(result?.status || 'unknown');
    this.state.authenticated = !!result?.authenticated;
    this.state.verificationUrl = String(result?.verification_url || this.state.verificationUrl || '');
    this.state.userCode = String(result?.user_code || this.state.userCode || '');
    this.state.requiresInput = !!result?.requires_input;
    this.state.running = this.state.status === 'pending';
    this.state.error = '';
    if (!this.state.running) {
      this.state.verificationUrl = '';
      this.state.userCode = '';
      this.state.requiresInput = false;
      this.state.input = '';
    }
    this.cdr.detectChanges();
    if (this.state.running) {
      this.schedulePoll();
    } else {
      this.clearPoll();
      if (this.state.authenticated) {
        this.notifications.success(`${this.providerName} erfolgreich angemeldet`);
      }
    }
  }

  private schedulePoll() {
    this.clearPoll();
    this.pollTimer = setTimeout(() => {
      const baseUrl = this.hubUrl();
      if (!baseUrl || !this.workerName || !this.state.sessionId || !this.state.running) return;
      this.agentApi.sgptBackendWorkerAction(baseUrl, this.backendId, {
        worker_name: this.workerName,
        action: 'login_status',
        session_id: this.state.sessionId,
      }).subscribe({
        next: (result: any) => this.applyResult(result),
        error: (error: any) => this.fail(error),
      });
    }, 1500);
  }

  private fail(error: any) {
    this.clearPoll();
    this.state.running = false;
    this.state.status = 'failed';
    this.state.error = String(error?.error?.message || error?.message || error);
    this.notifications.error('Account-Login fehlgeschlagen: ' + this.state.error);
    this.cdr.detectChanges();
  }

  private authConfig(): Record<string, unknown> {
    if (this.backendId === 'codex') {
      return { codex_cli: { auth_mode: 'chatgpt_login' } };
    }
    return {
      claude_cli: {
        enabled: this.claudeEnabled,
        auth_mode: 'claude_login',
        permission_mode: this.claudePermissionMode,
        timeout_seconds: this.claudeTimeoutSeconds,
      },
    };
  }

  private hubUrl(): string | null {
    return this.system.resolveHubAgent()?.url || null;
  }

  private reset() {
    this.clearPoll();
    this.state = emptyState();
  }

  private clearPoll() {
    if (this.pollTimer !== undefined) clearTimeout(this.pollTimer);
    this.pollTimer = undefined;
  }
}
