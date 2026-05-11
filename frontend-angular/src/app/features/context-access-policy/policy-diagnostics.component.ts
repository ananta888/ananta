import { Component, Input, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';
import { PreviewDecisionComponent } from './preview-decision.component';

@Component({
  selector: 'app-policy-diagnostics',
  standalone: true,
  imports: [CommonModule, PreviewDecisionComponent],
  template: `
    <div class="diagnostics-container p-3">
      <h4>Richtlinien-Diagnose & Debugger (T019)</h4>
      <div class="row">
        <div class="col-lg-5">
          <app-preview-decision [baseUrl]="baseUrl"></app-preview-decision>
        </div>
        <div class="col-lg-7">
          <div class="card shadow-sm h-100">
            <div class="card-header bg-dark text-white">
              <h6 class="mb-0">System-Status & Diagnosen</h6>
            </div>
            <div class="card-body">
              <div *ngIf="diagnostics$ | async as diag; else loading">
                <div class="alert alert-info py-2 small">
                  <strong>Projekt:</strong> {{ projectId }}<br>
                  <strong>Aktive Policy:</strong> {{ diag.active_policy_id || 'Keine' }} (v{{ diag.active_policy_version || '0' }})
                </div>

                <h6>Identifizierte Konflikte</h6>
                <ul class="list-group list-group-flush mb-3">
                  <li *ngFor="let item of diag.conflicts" class="list-group-item small py-1 px-0">
                    <span class="badge bg-warning text-dark me-2">Konflikt</span>
                    {{ item }}
                  </li>
                  <li *ngIf="diag.conflicts?.length === 0" class="list-group-item small text-muted py-1 px-0 border-0">
                    Keine direkten Regel-Konflikte gefunden.
                  </li>
                </ul>

                <h6>Unerreichbare Regeln</h6>
                <ul class="list-group list-group-flush mb-3">
                  <li *ngFor="let ruleId of diag.unreachable_rules" class="list-group-item small py-1 px-0">
                    <span class="badge bg-secondary me-2">Shadowed</span>
                    Regel <code>{{ ruleId }}</code> wird niemals erreicht.
                  </li>
                  <li *ngIf="diag.unreachable_rules?.length === 0" class="list-group-item small text-muted py-1 px-0 border-0">
                    Alle Regeln sind theoretisch erreichbar.
                  </li>
                </ul>

                <h6>Sicherheits-Empfehlungen</h6>
                <div *ngFor="let rec of diag.recommendations" class="alert alert-light border small p-2 mb-2">
                   <i class="bi bi-lightbulb text-warning me-2"></i> {{ rec }}
                </div>
              </div>
              <ng-template #loading>
                <div class="text-center py-5">
                  <div class="spinner-grow text-info" role="status"></div>
                </div>
              </ng-template>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .diagnostics-container { background-color: #f0f2f5; min-height: 100vh; }
  `]
})
export class PolicyDiagnosticsComponent implements OnInit {
  private api = inject(ContextAccessPolicyApiService);

  @Input() baseUrl = '';
  @Input() projectId = 'default-project';

  diagnostics$: any;

  ngOnInit(): void {
    this.loadDiagnostics();
  }

  loadDiagnostics(): void {
    this.diagnostics$ = this.api.getDiagnostics(this.baseUrl, this.projectId);
  }
}
