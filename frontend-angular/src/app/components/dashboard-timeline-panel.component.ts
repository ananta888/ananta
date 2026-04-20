import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AgentEntry, TeamEntry, TimelineEvent } from '../models/dashboard.models';
import { DecisionExplanationComponent, NextStepsComponent, NextStepAction, SafetyNoticeComponent } from '../shared/ui/display';
import { FormFieldComponent } from '../shared/ui/forms';
import { SectionCardComponent } from '../shared/ui/layout';

@Component({
  standalone: true,
  selector: 'app-dashboard-timeline-panel',
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    FormFieldComponent,
    SafetyNoticeComponent,
    SectionCardComponent,
    DecisionExplanationComponent,
    NextStepsComponent,
  ],
  template: `
    <app-section-card class="block mt-md" title="Live Decision Timeline">
      <div class="grid cols-4 mt-sm">
        <app-form-field label="Team">
          <select [ngModel]="teamId" (ngModelChange)="teamIdChange.emit($event); refresh.emit()" aria-label="Timeline Team-Filter">
            <option value="">Alle</option>
            @for (t of teams; track t) {
              <option [value]="t.id">{{ t.name }}</option>
            }
          </select>
        </app-form-field>
        <app-form-field label="Agent">
          <select [ngModel]="agent" (ngModelChange)="agentChange.emit($event); refresh.emit()" aria-label="Timeline Agent-Filter">
            <option value="">Alle</option>
            @for (a of agents; track a) {
              <option [value]="a.url">{{ a.name }}</option>
            }
          </select>
        </app-form-field>
        <app-form-field label="Status">
          <select [ngModel]="status" (ngModelChange)="statusChange.emit($event); refresh.emit()" aria-label="Timeline Status-Filter">
            <option value="">Alle</option>
            <option value="todo">todo</option>
            <option value="assigned">assigned</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
            <option value="blocked">blocked</option>
          </select>
        </app-form-field>
        <app-form-field label="Filter" [inline]="true">
          <input type="checkbox" [ngModel]="errorOnly" (ngModelChange)="errorOnlyChange.emit($event); refresh.emit()" aria-label="Timeline nur Fehler anzeigen" />
          Nur Fehler
        </app-form-field>
      </div>
      <div class="muted font-sm mt-sm">Eintraege: {{ items.length }}</div>
      <div class="timeline-container mt-sm">
        @for (ev of items; track ev) {
          <div class="list-item">
            <div class="row space-between">
              <div class="row gap-sm">
                <strong>{{ ev.event_type }}</strong>
                @if (isGuardrailEvent(ev)) {
                  <span class="badge danger">Guardrail Block</span>
                }
              </div>
              <span class="muted">{{ (ev.timestamp || 0) * 1000 | date:'HH:mm:ss' }}</span>
            </div>
            <div class="muted font-sm">
              Task: <a [routerLink]="['/task', ev.task_id]">{{ ev.task_id }}</a> |
              Agent: {{ shortActor(ev.actor) }} |
              Status: {{ ev.task_status || '-' }}
            </div>
            @if (ev.details?.reason) {
              <div class="font-sm mt-sm">Grund: {{ ev.details.reason }}</div>
            }
            @if (isGuardrailEvent(ev)) {
              <app-safety-notice class="block mt-sm" title="Guardrail Block" [message]="'Blockierte Tools: ' + guardrailBlockedToolsCount(ev)"></app-safety-notice>
              <app-decision-explanation class="block mt-sm" kind="tool-approval" title="Warum wurde das blockiert?"></app-decision-explanation>
              <app-next-steps class="block mt-sm" [steps]="guardrailNextSteps(ev)" title="Naechste Schritte"></app-next-steps>
            }
            @if (isGuardrailEvent(ev) && guardrailReasonsText(ev)) {
              <div class="muted font-sm mt-sm">
                Regeln: {{ guardrailReasonsText(ev) }}
              </div>
            }
            @if (ev.details?.output_preview) {
              <div class="muted font-sm mt-sm">Ergebnis: {{ ev.details.output_preview }}</div>
            }
          </div>
        }
        @if (!items.length) {
          <div class="list-item muted">Keine Timeline-Eintraege fuer aktuellen Filter.</div>
        }
      </div>
    </app-section-card>
  `,
})
export class DashboardTimelinePanelComponent {
  @Input() items: TimelineEvent[] = [];
  @Input() teams: TeamEntry[] = [];
  @Input() agents: AgentEntry[] = [];

  @Input() teamId = '';
  @Output() teamIdChange = new EventEmitter<string>();

  @Input() agent = '';
  @Output() agentChange = new EventEmitter<string>();

  @Input() status = '';
  @Output() statusChange = new EventEmitter<string>();

  @Input() errorOnly = false;
  @Output() errorOnlyChange = new EventEmitter<boolean>();

  @Output() refresh = new EventEmitter<void>();

  isGuardrailEvent(ev: TimelineEvent): boolean {
    return String(ev?.event_type || '').toLowerCase() === 'tool_guardrail_blocked';
  }

  guardrailBlockedToolsCount(ev: TimelineEvent): number {
    const blockedTools = ev?.details?.blocked_tools;
    return Array.isArray(blockedTools) ? blockedTools.length : 0;
  }

  guardrailReasonsText(ev: TimelineEvent): string {
    const reasons = ev?.details?.blocked_reasons;
    return Array.isArray(reasons) ? reasons.join(', ') : '';
  }

  shortActor(actor: string | undefined): string {
    if (!actor) return 'system';
    const match = this.agents.find(a => a.url === actor);
    if (match?.name) return match.name;
    return actor.replace(/^https?:\/\//, '');
  }

  guardrailNextSteps(ev: TimelineEvent): NextStepAction[] {
    const taskId = String(ev?.task_id || '').trim();
    return [
      { id: 'open-task', label: 'Task oeffnen', description: 'Details, Status und Kontext pruefen.', routerLink: taskId ? ['/task', taskId] : ['/board'] },
      { id: 'open-settings', label: 'Policies pruefen', description: 'Exposure/Tool-Grenzen und Governance-Profile einsehen.', routerLink: ['/settings'] },
      { id: 'open-board', label: 'Board oeffnen', description: 'Blockierte Tasks gesammelt sichten.', routerLink: ['/board'] },
    ];
  }
}
