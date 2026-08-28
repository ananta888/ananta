import { Component, EventEmitter, Input, Output } from '@angular/core';

import { FormsModule } from '@angular/forms';

import { AssistantRuntimeContext, CliBackend } from './ai-assistant.types';

@Component({
  standalone: true,
  selector: 'app-ai-assistant-controls',
  imports: [FormsModule],
  template: `
    <div class="backend-picker">
      <label>
        Coding-Client
        <select
          data-testid="assistant-cli-backend"
          [ngModel]="cliBackend"
          (ngModelChange)="cliBackendChange.emit($event)">
          @for (backend of availableCliBackends; track backend) {
            <option [value]="backend">{{ backendOptionLabel(backend) }}</option>
          }
        </select>
      </label>
      <small class="backend-explanation">{{ selectedBackendExplanation() }}</small>
    </div>
    <div class="input-area">
      <input
        data-testid="assistant-dock-input"
        [ngModel]="chatInput"
        (ngModelChange)="chatInputChange.emit($event)"
        (keyup.enter)="send.emit()"
        placeholder="Ask me anything..."
        [disabled]="busy">
      <button (click)="send.emit()" [disabled]="busy || !chatInput.trim()">Send</button>
      @if (lastFailedRequest && !busy) {
        <button class="cancel-btn" (click)="retryLast.emit()">Retry last</button>
      }
    </div>
  `,
  styles: [`
    .input-area { display: flex; gap: 5px; }
    .backend-picker { display: grid; grid-template-columns: minmax(180px, 260px) 1fr; gap: 8px; align-items: end; margin-bottom: 8px; }
    .backend-picker label { display: grid; gap: 3px; font-size: 12px; }
    .backend-explanation { opacity: 0.75; padding-bottom: 4px; }
    .hybrid-toggle { display: block; margin-top: 8px; font-size: 12px; }
    .mini-btn { font-size: 11px; padding: 2px 6px; border: 1px solid var(--border); background: transparent; color: var(--fg); cursor: pointer; }
    .cancel-btn { background: #dc3545; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
    @media (max-width: 900px) {
      .input-area { flex-wrap: wrap; }
      .input-area input { width: 100%; }
    }
  `]
})
export class AiAssistantControlsComponent {
  @Input() busy = false;
  @Input() chatInput = '';
  @Input() useHybridContext = false;
  @Input() cliBackend: CliBackend = 'auto';
  @Input() availableCliBackends: CliBackend[] = [];
  @Input() backendMetadata: Record<string, any> = {};
  @Input() selectedCliRuntime: any = null;
  @Input() lastFailedRequest?: { mode: 'hybrid' | 'chat'; prompt: string };
  @Input() runtimeContext: AssistantRuntimeContext = {
    route: '/',
    agents: [],
    teamsCount: 0,
    templatesCount: 0,
    templatesSummary: [],
    editableSettings: [],
    hasConfig: false,
  };
  @Input() quickActions: Array<{ label: string; prompt: string }> = [];

  @Output() chatInputChange = new EventEmitter<string>();
  @Output() useHybridContextChange = new EventEmitter<boolean>();
  @Output() send = new EventEmitter<void>();
  @Output() retryLast = new EventEmitter<void>();
  @Output() refreshContext = new EventEmitter<void>();
  @Output() quickAction = new EventEmitter<string>();
  @Output() cliBackendChange = new EventEmitter<CliBackend>();

  backendLabel(backend: CliBackend): string {
    if (backend === 'sgpt') return 'ShellGPT';
    if (backend === 'codex') return 'Codex CLI';
    if (backend === 'opencode') return 'OpenCode';
    if (backend === 'claude_code') return 'Claude Code';
    if (backend === 'aider') return 'Aider';
    if (backend === 'mistral_code') return 'Mistral Code';
    if (backend === 'qwen_code') return 'Qwen Code';
    if (backend === 'gemini_cli') return 'Gemini CLI';
    if (backend === 'copilot_cli') return 'GitHub Copilot CLI';
    if (backend === 'cline') return 'Cline';
    if (backend === 'kilo_code') return 'Kilo Code';
    return 'Auto';
  }

  backendOptionLabel(backend: CliBackend): string {
    if (backend === 'auto') return 'Auto (Free-first Policy)';
    const metadata = this.backendMetadata?.[backend] || {};
    const freeClass = this.freeClassLabel(metadata.free_class);
    const status = metadata.available === false ? ' · nicht installiert' : '';
    return `${this.backendLabel(backend)} · ${freeClass}${status}`;
  }

  selectedBackendExplanation(): string {
    if (this.cliBackend === 'auto') {
      return 'Hub-Auswahl: Free-/Quota-Klasse zuerst; Paid-Fallback bleibt standardmäßig gesperrt.';
    }
    const metadata = this.backendMetadata?.[this.cliBackend] || {};
    const inferenceTarget = this.selectedCliRuntime?.target_provider || 'CLI-Account bzw. konfigurierter Modellprovider';
    const availability = metadata.available === false
      ? `Nicht verfügbar: ${metadata.install_hint || 'CLI ist auf diesem Laufzeitziel nicht installiert.'}`
      : 'verfügbar oder noch nicht geprüft';
    return `Client: ${this.backendLabel(this.cliBackend)} · Inferenz/Account: ${this.freeClassLabel(metadata.free_class)} · Ziel: ${inferenceTarget} · ${availability}`;
  }

  private freeClassLabel(value: string): string {
    if (value === 'included_free_inference') return 'Inferenz inklusive';
    if (value === 'free_tier_limited') return 'limitierter Free-Tier';
    if (value === 'open_source_byok') return 'Open Source / BYOK';
    if (value === 'paid_or_unknown') return 'Paid oder unbekannt';
    return 'Kostenklasse unbekannt';
  }
}
