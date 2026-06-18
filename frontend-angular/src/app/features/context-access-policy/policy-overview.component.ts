import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';
import { EffectivePolicyReadModel, PolicyTemplate, ContextAccessPolicy } from '../../models/context-access-policy.model';
import { ContextPolicyDiagnostics } from '../../models/context-policy-diagnostics.model';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { PolicyPresetSelectorComponent } from './policy-preset-selector.component';
import { PolicyLintPanelComponent } from './policy-lint-panel.component';
import { PolicyVersionListComponent } from './policy-version-list.component';
import { PreviewDecisionComponent } from './preview-decision.component';

@Component({
  selector: 'app-policy-overview',
  standalone: true,
  imports: [
    CommonModule, 
    PolicyPresetSelectorComponent, 
    PolicyLintPanelComponent, 
    PolicyVersionListComponent,
    PreviewDecisionComponent
  ],
  template: `
    <div class="policy-overview-container">
      <div class="header">
        <h2>Context Access Policy Übersicht</h2>
        <div class="actions">
          <button class="btn btn-primary" (click)="editDraft()">Draft bearbeiten</button>
          <button class="btn btn-secondary" (click)="lint()">Prüfen (Lint)</button>
        </div>
      </div>

      <div *ngIf="diagnostics$ | async as diag; else loading" class="dashboard">
        <div class="summary-cards">
          <div class="card shadow-sm">
            <div class="card-body">
              <h5 class="card-title">Aktive Policy</h5>
              <p class="card-text">ID: {{ diag.active_policy_id }}</p>
              <p class="card-text">Version: {{ diag.active_policy_version }}</p>
              <span class="badge" [ngClass]="diag.bypass_mode_active ? 'bg-danger' : 'bg-success'">
                {{ diag.bypass_mode_active ? 'Bypass Aktiv' : 'Enforcement Aktiv' }}
              </span>
            </div>
          </div>

          <div class="card shadow-sm">
            <div class="card-body">
              <h5 class="card-title">Cloud & Externe Worker</h5>
              <p class="card-text">Cloud Worker: {{ diag.configured_cloud_workers }}</p>
              <p class="card-text">Externe Worker: {{ diag.configured_external_workers }}</p>
            </div>
          </div>

          <div class="card shadow-sm">
            <div class="card-body">
              <h5 class="card-title">Abgelehnte Anfragen (Recent)</h5>
              <ul class="list-unstyled">
                <li *ngFor="let denial of diag.recent_denials">
                  <span class="badge bg-warning text-dark">{{ denial.reason_code }}</span>: {{ denial.count }}
                </li>
                <li *ngIf="diag.recent_denials.length === 0">Keine Ablehnungen</li>
              </ul>
            </div>
          </div>
        </div>

        <div class="row mt-4">
          <div class="col-md-6">
            <app-policy-preset-selector [baseUrl]="baseUrl" (presetSelected)="onPresetSelected($event)"></app-policy-preset-selector>
          </div>
          <div class="col-md-6">
            <app-preview-decision [baseUrl]="baseUrl"></app-preview-decision>
          </div>
        </div>

        <div class="row mt-4">
          <div class="col-12">
            <app-policy-lint-panel [lintResult]="diag.lint_status"></app-policy-lint-panel>
          </div>
        </div>

        <div class="row mt-4">
          <div class="col-12">
            <app-policy-version-list [baseUrl]="baseUrl" [projectId]="projectId"></app-policy-version-list>
          </div>
        </div>

        <div class="alerts mt-4">
          <div *ngIf="diag.has_default_policy_fallback" class="alert alert-warning">
            <strong>Warnung:</strong> Fallback-Policy ist aktiv. Keine projektspezifische Richtlinie konfiguriert.
          </div>
          <div *ngIf="diag.last_decision_error" class="alert alert-danger">
            Letzter Fehler: {{ diag.last_decision_error }}
          </div>
        </div>
      </div>

      <ng-template #loading>
        <div class="text-center mt-5">
          <div class="spinner-border text-primary" role="status">
            <span class="visually-hidden">Lade Diagnose-Daten...</span>
          </div>
        </div>
      </ng-template>
    </div>
  `,
  styles: [`
    .policy-overview-container { padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .dashboard { display: flex; flex-direction: column; gap: 20px; }
    .summary-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
    .card { min-height: 150px; }
  `]
})
export class PolicyOverviewComponent implements OnInit {
  private api = inject(ContextAccessPolicyApiService);
  
  // In einer echten App würden wir die ProjectId aus einem Store oder Route bekommen.
  // Hier nutzen wir einen Platzhalter.
  projectId = 'default-project';
  baseUrl = ''; // Wird im CoreService gehandelt, wenn leer.

  diagnostics$!: Observable<ContextPolicyDiagnostics>;

  ngOnInit(): void {
    this.loadDiagnostics();
  }

  loadDiagnostics(): void {
    this.diagnostics$ = this.api.getDiagnostics(this.baseUrl, this.projectId).pipe(
      catchError(err => {
        console.error('Fehler beim Laden der Diagnosedaten', err);
        return of({
          active_policy_id: 'n/a',
          active_policy_version: 0,
          has_default_policy_fallback: true,
          configured_cloud_workers: 0,
          configured_external_workers: 0,
          bypass_mode_active: false,
          degraded_mode_active: false,
          recent_denials: [],
          lint_status: { is_valid: true, errors: [], warnings: [], infos: [] }
        } as ContextPolicyDiagnostics);
      })
    );
  }

  editDraft(): void {
    // Navigiere zum Editor (CAP-FE-M3)
    console.log('Edit draft');
  }

  lint(): void {
    console.log('Linting policy');
  }

  onPresetSelected(tpl: PolicyTemplate): void {
    console.log('Preset selected:', tpl);
    this.api.applyTemplate(this.baseUrl, this.projectId, tpl.id).subscribe({
      next: () => {
        this.loadDiagnostics();
        alert(`Template "${tpl.name}" wurde erfolgreich angewendet.`);
      },
      error: (err) => alert('Fehler beim Anwenden des Templates: ' + err.message)
    });
  }
}
