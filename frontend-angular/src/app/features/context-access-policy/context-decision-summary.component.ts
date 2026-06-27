import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextBlockAccessDecision } from '../../models/context-access-policy.model';

@Component({
  selector: 'app-context-decision-summary',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="context-decision-summary mt-3">
      <h6>Daten-Grenzwerte & Zugriffsschutz</h6>
      <div class="table-responsive">
        <table class="table table-sm table-bordered">
          <thead class="table-light">
            <tr>
              <th>Quelle</th>
              <th>Sensitivität</th>
              <th>Entscheidung</th>
              <th>Grund</th>
            </tr>
          </thead>
          <tbody>
            @for (d of decisions; track d) {
              <tr>
                <td class="small">{{ d.source_ref }}</td>
                <td>
                  <span class="badge bg-light text-dark border">{{ d.effective_sensitivity }}</span>
                </td>
                <td>
                  <span class="badge" [ngClass]="getDecisionClass(d.decision)">
                    {{ d.decision }}
                  </span>
                </td>
                <td class="small text-muted">{{ d.reason_code }}</td>
              </tr>
            }
            @if (decisions.length === 0) {
              <tr>
                <td colspan="4" class="text-center text-muted">Keine Kontext-Entscheidungen protokolliert.</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </div>
    `,
  styles: [`
    .context-decision-summary { border-top: 1px solid #dee2e6; padding-top: 15px; }
  `]
})
export class ContextDecisionSummaryComponent {
  @Input() decisions: ContextBlockAccessDecision[] = [];

  getDecisionClass(d: string): string {
    switch (d) {
      case 'allow': return 'bg-success';
      case 'deny': return 'bg-danger';
      case 'allow_redacted':
      case 'allow_summary_only': return 'bg-info text-white';
      case 'approval_required': return 'bg-warning text-dark';
      default: return 'bg-secondary';
    }
  }
}
