import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';

@Component({
  selector: 'app-global-policy-dashboard',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="global-dashboard p-4">
      <h3 class="mb-4">Globales Daten-Grenzschutz Dashboard (T022)</h3>
    
      <div class="row g-4 mb-4">
        <div class="col-md-3">
          <div class="card bg-primary text-white shadow-sm h-100">
            <div class="card-body text-center">
              <h1 class="display-4 fw-bold">{{ stats.protectedProjects }}</h1>
              <p class="mb-0">Geschützte Projekte</p>
            </div>
          </div>
        </div>
        <div class="col-md-3">
          <div class="card bg-danger text-white shadow-sm h-100">
            <div class="card-body text-center">
              <h1 class="display-4 fw-bold">{{ stats.unprotectedProjects }}</h1>
              <p class="mb-0">Unprotected Projects</p>
            </div>
          </div>
        </div>
        <div class="col-md-3">
          <div class="card bg-warning text-dark shadow-sm h-100">
            <div class="card-body text-center">
              <h1 class="display-4 fw-bold">{{ stats.activeViolations }}</h1>
              <p class="mb-0">Aktive Verstöße (24h)</p>
            </div>
          </div>
        </div>
        <div class="col-md-3">
          <div class="card bg-success text-white shadow-sm h-100">
            <div class="card-body text-center">
              <h1 class="display-4 fw-bold">{{ stats.localOnlyPercent }}%</h1>
              <p class="mb-0">Local-Only Execution</p>
            </div>
          </div>
        </div>
      </div>
    
      <div class="card shadow-sm">
        <div class="card-header bg-white">
          <h5 class="mb-0">Projekt-Status Übersicht</h5>
        </div>
        <div class="card-body p-0">
          <table class="table table-hover mb-0">
            <thead>
              <tr>
                <th>Projekt</th>
                <th>Aktive Richtlinie</th>
                <th>Risiko-Profil</th>
                <th>Verstöße</th>
                <th>Aktionen</th>
              </tr>
            </thead>
            <tbody>
              @for (p of projectStats; track p) {
                <tr>
                  <td><strong>{{ p.name }}</strong></td>
                  <td>
                    <span class="badge" [ngClass]="p.policyActive ? 'bg-success' : 'bg-secondary'">
                      {{ p.policyActive ? 'Aktiv' : 'Inaktiv' }}
                    </span>
                    <small class="ms-1 text-muted">v{{ p.version }}</small>
                  </td>
                  <td>
                    <span class="badge" [ngClass]="getRiskClass(p.riskLevel)">
                      {{ p.riskLevel | uppercase }}
                    </span>
                  </td>
                  <td>
                    @if (p.violations > 0) {
                      <span class="badge bg-danger rounded-pill">{{ p.violations }}</span>
                    }
                    @if (p.violations === 0) {
                      <span class="text-success small"><i class="bi bi-check-circle"></i></span>
                    }
                  </td>
                  <td>
                    <button class="btn btn-sm btn-outline-primary">Konfigurieren</button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </div>
    </div>
    `,
  styles: []
})
export class GlobalPolicyDashboardComponent {
  private api = inject(ContextAccessPolicyApiService);

  stats = {
    protectedProjects: 8,
    unprotectedProjects: 2,
    activeViolations: 14,
    localOnlyPercent: 78
  };

  projectStats = [
    { name: 'Core Engine', policyActive: true, version: 12, riskLevel: 'low', violations: 0 },
    { name: 'Customer Portal', policyActive: true, version: 5, riskLevel: 'high', violations: 3 },
    { name: 'Legacy Auth', policyActive: false, version: 0, riskLevel: 'critical', violations: 11 },
    { name: 'Internal Tools', policyActive: true, version: 2, riskLevel: 'medium', violations: 0 }
  ];

  getRiskClass(level: string): string {
    switch (level) {
      case 'low': return 'bg-success';
      case 'medium': return 'bg-info text-dark';
      case 'high': return 'bg-warning text-dark';
      case 'critical': return 'bg-danger';
      default: return 'bg-secondary';
    }
  }
}
