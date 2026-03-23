import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { AssistantRuntimeContext, CliBackend } from './ai-assistant.types';

@Component({
  standalone: true,
  selector: 'app-ai-assistant-controls',
  imports: [CommonModule, FormsModule],
  template: `
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
    <label class="hybrid-toggle">
      <input type="checkbox" [ngModel]="useHybridContext" (ngModelChange)="useHybridContextChange.emit($event)" [disabled]="busy">
      Hybrid Context (Aider + Vibe + LlamaIndex)
    </label>
    <div class="muted context-info">Route: {{ runtimeContext.route }} | User: {{ runtimeContext.userName || 'n/a' }} ({{ runtimeContext.userRole || 'n/a' }}) | Agents: {{ runtimeContext.agents.length }} | Teams: {{ runtimeContext.teamsCount }} | Templates: {{ runtimeContext.templatesCount }}</div>
    <div class="row quick-actions-row">
      <button class="mini-btn" (click)="refreshContext.emit()">Refresh Context</button>
      @for (qa of quickActions; track qa.label) {
        <button class="mini-btn" (click)="quickAction.emit(qa.prompt)" [disabled]="busy">{{ qa.label }}</button>
      }
    </div>
    <label class="hybrid-toggle">
      CLI Backend:
      <select [ngModel]="cliBackend" (ngModelChange)="cliBackendChange.emit($event)" [disabled]="busy">
        @for (backend of availableCliBackends; track backend) {
          <option [value]="backend">{{ backendLabel(backend) }}</option>
        }
      </select>
    </label>
    @if (selectedCliRuntime) {
      <div class="muted context-info">
        Runtime: {{ selectedCliRuntime?.binary_available ? 'binary ok' : 'binary missing' }} |
        Health: {{ selectedCliRuntime?.health_score ?? 'n/a' }}
        @if (selectedCliRuntime?.target_base_url) {
          | Target: {{ selectedCliRuntime?.target_is_local ? 'local' : 'remote' }} ({{ selectedCliRuntime?.target_base_url }})
        }
      </div>
    }
    <div class="muted context-info">Actions require admin rights and confirmation.</div>
  `,
  styles: [`
    .input-area { display: flex; gap: 5px; }
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
    if (backend === 'aider') return 'Aider';
    if (backend === 'mistral_code') return 'Mistral Code';
    return 'Auto';
  }
}
