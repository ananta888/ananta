import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TooltipDirective } from '../directives/tooltip.directive';
import { AutopilotSecurityLevel, AutopilotStatus, TeamEntry } from '../models/dashboard.models';
import { SummaryMetric, SummaryPanelComponent } from '../shared/ui/display';
import { FormFieldComponent } from '../shared/ui/forms';
import { SectionCardComponent } from '../shared/ui/layout';

export interface AutopilotStartPayload {
  goal: string;
  teamId: string;
  intervalSeconds: number;
  maxConcurrency: number;
  budgetLabel: string;
  securityLevel: AutopilotSecurityLevel;
}

@Component({
  standalone: true,
  selector: 'app-dashboard-autopilot-panel',
  imports: [CommonModule, FormsModule, TooltipDirective, FormFieldComponent, SectionCardComponent, SummaryPanelComponent],
  template: `
    <app-section-card class="block mt-md" title="Autopilot Control Center" subtitle="Steuerung fuer den kontinuierlichen Scrum-Team-Lauf.">
      <span section-actions class="help-icon" [appTooltip]="'Der Autopilot fuehrt Tasks automatisch in regelmaessigen Abstaenden aus.'" tabindex="0">?</span>

      <div class="grid cols-2 mt-sm">
        <app-form-field label="Sprint Goal">
          <input [ngModel]="goal" (ngModelChange)="goalChange.emit($event)" placeholder="z.B. MVP Login + Team Setup" aria-label="Autopilot Sprint Goal" />
        </app-form-field>
        <app-form-field label="Team">
          <select [ngModel]="teamId" (ngModelChange)="teamIdChange.emit($event)" aria-label="Autopilot Team auswaehlen">
            <option value="">Aktives Team</option>
            @for (t of teams; track t) {
              <option [value]="t.id">{{ t.name }}</option>
            }
          </select>
        </app-form-field>
        <app-form-field label="Tick-Intervall (s)" hint="Zeit zwischen automatischen Ausfuehrungen in Sekunden.">
          <input type="number" min="3" [ngModel]="intervalSeconds" (ngModelChange)="intervalSecondsChange.emit($event)" aria-label="Autopilot Tick-Intervall in Sekunden" />
        </app-form-field>
        <app-form-field label="Max Parallelitaet" hint="Maximale Anzahl gleichzeitig ausgefuehrter Tasks.">
          <input type="number" min="1" [ngModel]="maxConcurrency" (ngModelChange)="maxConcurrencyChange.emit($event)" aria-label="Autopilot maximale Parallelitaet" />
        </app-form-field>
        <app-form-field label="Budget-Hinweis">
          <input [ngModel]="budgetLabel" (ngModelChange)="budgetLabelChange.emit($event)" placeholder="z.B. 2h / 10k tokens" aria-label="Autopilot Budget-Hinweis" />
        </app-form-field>
        <app-form-field label="Sicherheitslevel" hint="safe: Nur sichere Ops, balanced: Eingeschraenkt, aggressive: Alle Ops erlaubt">
          <select [ngModel]="securityLevel" (ngModelChange)="securityLevelChange.emit($event)" aria-label="Autopilot Sicherheitslevel">
            <option value="safe">safe</option>
            <option value="balanced">balanced</option>
            <option value="aggressive">aggressive</option>
          </select>
        </app-form-field>
      </div>

      <div class="row gap-sm mt-md">
        <button (click)="start.emit()" [disabled]="busy" aria-label="Autopilot starten">Start</button>
        <button class="secondary" (click)="stop.emit()" [disabled]="busy" aria-label="Autopilot stoppen">Stop</button>
        <button class="secondary" (click)="tick.emit()" [disabled]="busy" aria-label="Autopilot manuell ticken">Tick now</button>
        <button class="secondary" (click)="refresh.emit()" [disabled]="busy" aria-label="Autopilot Status aktualisieren">Refresh status</button>
      </div>

      @if (status) {
        <app-summary-panel class="block mt-md" title="Autopilot Status" [metrics]="statusMetrics()" [columns]="3"></app-summary-panel>
        <div class="muted status-text-sm-lg">
          Last tick: {{ status.last_tick_at ? (status.last_tick_at * 1000 | date:'HH:mm:ss') : '-' }} |
          Last error: {{ status.last_error || '-' }}
        </div>
      }
    </app-section-card>
  `,
})
export class DashboardAutopilotPanelComponent {
  @Input() status: AutopilotStatus | null = null;
  @Input() teams: TeamEntry[] = [];
  @Input() busy = false;

  @Input() goal = '';
  @Output() goalChange = new EventEmitter<string>();

  @Input() teamId = '';
  @Output() teamIdChange = new EventEmitter<string>();

  @Input() intervalSeconds = 20;
  @Output() intervalSecondsChange = new EventEmitter<number>();

  @Input() maxConcurrency = 2;
  @Output() maxConcurrencyChange = new EventEmitter<number>();

  @Input() budgetLabel = '';
  @Output() budgetLabelChange = new EventEmitter<string>();

  @Input() securityLevel: AutopilotSecurityLevel = 'safe';
  @Output() securityLevelChange = new EventEmitter<AutopilotSecurityLevel>();

  @Output() start = new EventEmitter<void>();
  @Output() stop = new EventEmitter<void>();
  @Output() tick = new EventEmitter<void>();
  @Output() refresh = new EventEmitter<void>();

  statusMetrics(): SummaryMetric[] {
    return [
      { label: 'Status', value: this.status?.running ? 'running' : 'stopped', tone: this.status?.running ? 'success' : 'warning' },
      { label: 'Ticks', value: this.status?.tick_count || 0 },
      { label: 'Dispatched', value: this.status?.dispatched_count || 0 },
      { label: 'Completed/Failed', value: `${this.status?.completed_count || 0}/${this.status?.failed_count || 0}` },
    ];
  }
}
