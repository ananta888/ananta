import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TooltipDirective } from '../directives/tooltip.directive';
import { AutopilotSecurityLevel, AutopilotStatus, TeamEntry } from '../models/dashboard.models';

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
  imports: [CommonModule, FormsModule, TooltipDirective],
  template: `
    <div class="card mt-md">
      <h3>Autopilot Control Center <span class="help-icon" [appTooltip]="'Der Autopilot fuehrt Tasks automatisch in regelmaessigen Abstaenden aus.'" tabindex="0">?</span></h3>
      <p class="muted mt-sm">Steuerung fuer den kontinuierlichen Scrum-Team-Lauf.</p>

      <div class="grid cols-2 mt-sm">
        <label>
          Sprint Goal
          <input [ngModel]="goal" (ngModelChange)="goalChange.emit($event)" placeholder="z.B. MVP Login + Team Setup" aria-label="Autopilot Sprint Goal" />
        </label>
        <label>
          Team
          <select [ngModel]="teamId" (ngModelChange)="teamIdChange.emit($event)" aria-label="Autopilot Team auswaehlen">
            <option value="">Aktives Team</option>
            @for (t of teams; track t) {
              <option [value]="t.id">{{ t.name }}</option>
            }
          </select>
        </label>
        <label>
          Tick-Intervall (s) <span class="help-icon" [appTooltip]="'Zeit zwischen automatischen Ausfuehrungen in Sekunden.'" tabindex="0">?</span>
          <input type="number" min="3" [ngModel]="intervalSeconds" (ngModelChange)="intervalSecondsChange.emit($event)" aria-label="Autopilot Tick-Intervall in Sekunden" />
        </label>
        <label>
          Max Parallelitaet <span class="help-icon" [appTooltip]="'Maximale Anzahl gleichzeitig ausgefuehrter Tasks.'" tabindex="0">?</span>
          <input type="number" min="1" [ngModel]="maxConcurrency" (ngModelChange)="maxConcurrencyChange.emit($event)" aria-label="Autopilot maximale Parallelitaet" />
        </label>
        <label>
          Budget-Hinweis
          <input [ngModel]="budgetLabel" (ngModelChange)="budgetLabelChange.emit($event)" placeholder="z.B. 2h / 10k tokens" aria-label="Autopilot Budget-Hinweis" />
        </label>
        <label>
          Sicherheitslevel <span class="help-icon" [appTooltip]="'safe: Nur sichere Ops, balanced: Eingeschraenkt, aggressive: Alle Ops erlaubt'" tabindex="0">?</span>
          <select [ngModel]="securityLevel" (ngModelChange)="securityLevelChange.emit($event)" aria-label="Autopilot Sicherheitslevel">
            <option value="safe">safe</option>
            <option value="balanced">balanced</option>
            <option value="aggressive">aggressive</option>
          </select>
        </label>
      </div>

      <div class="row gap-sm mt-md">
        <button (click)="start.emit()" [disabled]="busy" aria-label="Autopilot starten">Start</button>
        <button class="secondary" (click)="stop.emit()" [disabled]="busy" aria-label="Autopilot stoppen">Stop</button>
        <button class="secondary" (click)="tick.emit()" [disabled]="busy" aria-label="Autopilot manuell ticken">Tick now</button>
        <button class="secondary" (click)="refresh.emit()" [disabled]="busy" aria-label="Autopilot Status aktualisieren">Refresh status</button>
      </div>

      @if (status) {
        <div class="grid cols-4 mt-md">
          <div>
            <div class="muted">Status</div>
            <strong [class.success]="status.running" [class.danger]="!status.running">{{ status.running ? 'running' : 'stopped' }}</strong>
          </div>
          <div>
            <div class="muted">Ticks</div>
            <strong>{{ status.tick_count || 0 }}</strong>
          </div>
          <div>
            <div class="muted">Dispatched</div>
            <strong>{{ status.dispatched_count || 0 }}</strong>
          </div>
          <div>
            <div class="muted">Completed/Failed</div>
            <strong>{{ status.completed_count || 0 }}/{{ status.failed_count || 0 }}</strong>
          </div>
        </div>
        <div class="muted status-text-sm-lg">
          Last tick: {{ status.last_tick_at ? (status.last_tick_at * 1000 | date:'HH:mm:ss') : '-' }} |
          Last error: {{ status.last_error || '-' }}
        </div>
      }
    </div>
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
}
