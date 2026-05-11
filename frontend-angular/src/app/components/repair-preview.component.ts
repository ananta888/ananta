import { Component, Input, Output, EventEmitter, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HubApiService } from '../services/hub-api.service';
import { AgentDirectoryService } from '../services/agent-directory.service';

@Component({
  standalone: true,
  selector: 'app-repair-preview',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h3>Worker & Runtime Selektion</h3>

      @if (loading) {
        <div class="spinner"></div>
      } @else {
        <div class="grid cols-2 gap-md">
          <div class="selection-panel">
            <label>Selektions-Modus
              <select [(ngModel)]="policy.mode" (ngModelChange)="onPolicyChange()">
                <option value="automatic">Automatisch (Hub wählt)</option>
                <option value="fixed">Fest (Manuelle Zuweisung)</option>
                <option value="policy_ranked">Policy Ranked (Ranking nach Kriterien)</option>
              </select>
            </label>

            @if (policy.mode === 'fixed') {
              <label class="mt-md">Worker auswählen
                <select [(ngModel)]="policy.fixed_worker_id" (ngModelChange)="onPolicyChange()">
                  <option [ngValue]="null">-- Bitte wählen --</option>
                  @for (c of candidates; track c.worker_id) {
                    <option [value]="c.worker_id">{{ c.display_name || c.worker_id }} ({{ c.worker_kind }})</option>
                  }
                </select>
              </label>
            }

            <div class="policy-settings mt-md">
              <label class="teams-checkbox">
                <input type="checkbox" [(ngModel)]="policy.prefer_local" (ngModelChange)="onPolicyChange()"> Lokal bevorzugen
              </label>
              <label class="teams-checkbox">
                <input type="checkbox" [(ngModel)]="policy.allow_cloud" (ngModelChange)="onPolicyChange()"> Cloud erlauben
              </label>
              <label class="teams-checkbox">
                <input type="checkbox" [(ngModel)]="policy.require_code_context" (ngModelChange)="onPolicyChange()"> Code-Kontext erforderlich
              </label>
            </div>
          </div>

          <div class="decision-panel card card-light">
            <h4>Aktuelle Entscheidung</h4>
            @if (decision) {
              <div class="decision-summary">
                <div class="row flex-between">
                  <span>Status:</span>
                  <span class="badge" [class.success]="decision.decision_status === 'selected'" [class.danger]="decision.decision_status !== 'selected'">
                    {{ decision.decision_status }}
                  </span>
                </div>
                @if (decision.selected_worker_id) {
                  <div class="mt-sm">
                    <strong>{{ decision.selected_worker_id }}</strong><br>
                    <small class="muted">{{ decision.selected_worker_kind }} @ {{ decision.selected_runtime_target_id }}</small>
                  </div>
                  <div class="mt-sm italic font-sm">
                    "{{ decision.selected_reason }}"
                  </div>
                } @else {
                  <div class="mt-sm danger">Kein passender Worker gefunden.</div>
                }

                @if (decision.rejected_candidates?.length) {
                  <div class="mt-md">
                    <h5>Abgelehnte Kandidaten</h5>
                    <ul class="font-sm muted">
                      @for (r of decision.rejected_candidates; track r.worker_id) {
                        <li>{{ r.worker_id || r.runtime_target_id }}: {{ r.reason_code }}</li>
                      }
                    </ul>
                  </div>
                }
              </div>
            } @else {
              <p class="muted">Keine Selektion verfügbar.</p>
            }
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .selection-panel { border-right: 1px solid #eee; padding-right: 15px; }
    .decision-panel { background: #f9f9f9; }
    .mt-sm { margin-top: 5px; }
    .mt-md { margin-top: 15px; }
    .italic { font-style: italic; }
    .font-sm { font-size: 0.85em; }
  `]
})
export class RepairPreviewComponent implements OnInit {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);

  @Input() plan: any;
  @Input() policy: any = {
    mode: 'automatic',
    prefer_local: true,
    allow_cloud: false,
    require_code_context: false,
    fixed_worker_id: null
  };

  @Output() policyUpdated = new EventEmitter<any>();

  candidates: any[] = [];
  decision: any = null;
  loading = false;
  hub = this.dir.list().find(a => a.role === 'hub');

  ngOnInit() {
    this.refreshCandidates();
  }

  refreshCandidates() {
    if (!this.hub) return;
    this.loading = true;
    this.hubApi.listRepairCandidates(this.hub.url, { policy: this.policy }).subscribe({
      next: (res) => {
        this.candidates = res.candidates || [];
        this.loading = false;
        this.previewSelection();
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  onPolicyChange() {
    this.policyUpdated.emit(this.policy);
    this.previewSelection();
  }

  previewSelection() {
    if (!this.hub || !this.plan) return;

    // Simuliere einen Preview-Call ans Backend
    const body = {
      matching_outcome: this.plan.matching_outcome || {},
      policy: this.policy,
      task_id: this.plan.task_id,
      goal_id: this.plan.goal_id
    };

    this.hubApi.previewRepair(this.hub.url, body).subscribe({
      next: (res) => {
        if (res.worker_selection) {
          this.decision = res.worker_selection.decision;
        }
      }
    });
  }
}
