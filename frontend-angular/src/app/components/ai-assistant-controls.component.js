var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
let AiAssistantControlsComponent = class AiAssistantControlsComponent {
    constructor() {
        this.busy = false;
        this.chatInput = '';
        this.useHybridContext = false;
        this.cliBackend = 'auto';
        this.availableCliBackends = [];
        this.selectedCliRuntime = null;
        this.runtimeContext = {
            route: '/',
            agents: [],
            teamsCount: 0,
            templatesCount: 0,
            templatesSummary: [],
            editableSettings: [],
            hasConfig: false,
        };
        this.quickActions = [];
        this.chatInputChange = new EventEmitter();
        this.useHybridContextChange = new EventEmitter();
        this.send = new EventEmitter();
        this.retryLast = new EventEmitter();
        this.refreshContext = new EventEmitter();
        this.quickAction = new EventEmitter();
        this.cliBackendChange = new EventEmitter();
    }
    backendLabel(backend) {
        if (backend === 'sgpt')
            return 'ShellGPT';
        if (backend === 'codex')
            return 'Codex CLI';
        if (backend === 'opencode')
            return 'OpenCode';
        if (backend === 'aider')
            return 'Aider';
        if (backend === 'mistral_code')
            return 'Mistral Code';
        return 'Auto';
    }
};
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "busy", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "chatInput", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "useHybridContext", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "cliBackend", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "availableCliBackends", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "selectedCliRuntime", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "lastFailedRequest", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "runtimeContext", void 0);
__decorate([
    Input()
], AiAssistantControlsComponent.prototype, "quickActions", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "chatInputChange", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "useHybridContextChange", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "send", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "retryLast", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "refreshContext", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "quickAction", void 0);
__decorate([
    Output()
], AiAssistantControlsComponent.prototype, "cliBackendChange", void 0);
AiAssistantControlsComponent = __decorate([
    Component({
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
], AiAssistantControlsComponent);
export { AiAssistantControlsComponent };
//# sourceMappingURL=ai-assistant-controls.component.js.map