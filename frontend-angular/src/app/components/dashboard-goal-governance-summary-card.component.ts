import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { GoalDetail, GoalGovernanceSummary, GoalListEntry } from '../models/dashboard.models';
import { UiSkeletonComponent } from './ui-skeleton.component';

@Component({
  standalone: true,
  selector: 'app-dashboard-goal-governance-summary-card',
  imports: [CommonModule, FormsModule, UiSkeletonComponent],
  template: `
    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Goal Governance & Cost Summary</h3>
          <div class="muted font-sm mt-sm">
            Verifikation, Policy-Entscheidungen und Ausfuehrungskosten des ausgewaehlten Goals.
          </div>
        </div>
        <div class="row gap-sm">
          <select
            aria-label="Goal fuer Governance Summary"
            [ngModel]="selectedGoalId"
            (ngModelChange)="selectGoal.emit($event)"
            [disabled]="loading || !goals.length"
          >
            @for (goal of goals; track goal.id) {
              <option [value]="goal.id">{{ goal.summary || goal.goal || goal.id }}</option>
            }
          </select>
          <button
            class="secondary"
            type="button"
            (click)="refresh.emit(selectedGoalId)"
            [disabled]="loading"
            aria-label="Goal Governance Summary aktualisieren"
          >
            Refresh
          </button>
        </div>
      </div>
      @if (loading) {
        <app-ui-skeleton [count]="4" [columns]="4" [lineCount]="1" [card]="false" containerClass="mt-sm" lineClass="skeleton line skeleton-40"></app-ui-skeleton>
      } @else if (goalDetail && goalGovernance) {
        <div class="muted font-sm mt-sm">
          Goal:
          <strong>{{ goalDetail?.goal?.summary || goalDetail?.goal?.goal || selectedGoalId }}</strong>
          <span> · Status: {{ goalDetail?.goal?.status || '-' }}</span>
          <span> · Tasks: {{ goalGovernance?.summary?.task_count || goalDetail?.tasks?.length || 0 }}</span>
        </div>
        <div class="grid cols-4 mt-sm">
          <div class="card card-light">
            <div class="muted">Verification</div>
            <strong>{{ goalGovernance?.verification?.passed || 0 }}/{{ goalGovernance?.verification?.total || 0 }}</strong>
            <div class="muted status-text-sm-alt">
              Failed: {{ goalGovernance?.verification?.failed || 0 }} · Escalated: {{ goalGovernance?.verification?.escalated || 0 }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Policy</div>
            <strong>{{ goalGovernance?.policy?.approved || 0 }}</strong>
            <div class="muted status-text-sm-alt">
              Approved · Blocked: {{ goalGovernance?.policy?.blocked || 0 }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Cost Units</div>
            <strong>{{ (goalGovernance?.cost_summary?.total_cost_units || 0) | number:'1.2-4' }}</strong>
            <div class="muted status-text-sm-alt">
              Tasks mit Cost: {{ goalGovernance?.cost_summary?.tasks_with_cost || 0 }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Tokens / Latenz</div>
            <strong>{{ goalGovernance?.cost_summary?.total_tokens || 0 }}</strong>
            <div class="muted status-text-sm-alt">
              {{ goalGovernance?.cost_summary?.total_latency_ms || 0 }} ms
            </div>
          </div>
        </div>
        @if (costTasks.length) {
          <div class="table-scroll mt-sm">
            <table class="standard-table table-min-600">
              <thead>
                <tr class="card-light">
                  <th>Task</th>
                  <th>Status</th>
                  <th>Verification</th>
                  <th>Cost</th>
                  <th>Tokens</th>
                </tr>
              </thead>
              <tbody>
                @for (task of costTasks; track task.id) {
                  <tr>
                    <td>
                      <div><strong>{{ task.title || task.id }}</strong></div>
                      <div class="muted font-sm">{{ task.id }}</div>
                    </td>
                    <td>{{ task.status || '-' }}</td>
                    <td>{{ task.verification_status?.status || '-' }}</td>
                    <td>{{ (task.cost_summary?.cost_units || 0) | number:'1.2-4' }}</td>
                    <td>{{ task.cost_summary?.tokens_total || 0 }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <div class="muted mt-sm">Fuer dieses Goal liegen noch keine taskbezogenen Cost-Summaries vor.</div>
        }
      } @else {
        <div class="muted mt-sm">Noch keine Goals fuer Governance- und Cost-Reporting vorhanden.</div>
      }
    </div>
  `,
})
export class DashboardGoalGovernanceSummaryCardComponent {
  @Input() goals: GoalListEntry[] = [];
  @Input() selectedGoalId = '';
  @Input() loading = false;
  @Input() goalDetail: GoalDetail | null = null;
  @Input() goalGovernance: GoalGovernanceSummary | null = null;
  @Input() costTasks: any[] = [];

  @Output() selectGoal = new EventEmitter<string>();
  @Output() refresh = new EventEmitter<string>();
}
