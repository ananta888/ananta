import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';

@Component({
  selector: 'app-policy-violation-log',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="violation-log card shadow-sm">
      <div class="card-header bg-danger text-white d-flex justify-content-between align-items-center">
        <h6 class="mb-0">Policy Violation Log (T021)</h6>
        <span class="badge bg-light text-danger">{{ violations.length }} Vorfälle</span>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm table-hover mb-0">
            <thead class="table-light">
              <tr>
                <th>Zeitpunkt</th>
                <th>Quelle</th>
                <th>Ziel</th>
                <th>Grund</th>
                <th>Aktion</th>
              </tr>
            </thead>
            <tbody>
              @for (v of violations; track v) {
                <tr class="small">
                  <td>{{ v.timestamp | date:'short' }}</td>
                  <td><code class="text-truncate d-inline-block" style="max-width: 150px;" [title]="v.source_ref">{{ v.source_ref }}</code></td>
                  <td>{{ v.worker_kind }} ({{ v.model_scope }})</td>
                  <td>
                    <span class="badge bg-danger-subtle text-danger border border-danger-subtle">
                      {{ v.reason_code }}
                    </span>
                  </td>
                  <td>
                    <button class="btn btn-xs btn-outline-primary py-0" (click)="viewDetails(v)">Details</button>
                  </td>
                </tr>
              }
              @if (violations.length === 0) {
                <tr>
                  <td colspan="5" class="text-center py-4 text-muted">Keine Verstöße in diesem Zeitraum.</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </div>
    </div>
    `,
  styles: [`
    .btn-xs { font-size: 0.75rem; padding: 0.1rem 0.3rem; }
  `]
})
export class PolicyViolationLogComponent {
  private api = inject(ContextAccessPolicyApiService);

  @Input() baseUrl = '';
  @Input() projectId = 'default-project';

  violations: any[] = [
    { 
      timestamp: new Date().toISOString(), 
      source_ref: 'secrets/api_key.txt', 
      worker_kind: 'hermes', 
      model_scope: 'public_cloud', 
      reason_code: 'secret_blocked' 
    },
    { 
      timestamp: new Date(Date.now() - 3600000).toISOString(), 
      source_ref: 'src/private/customer_data.csv', 
      worker_kind: 'remote_worker', 
      model_scope: 'private_remote', 
      reason_code: 'external_worker_blocked' 
    }
  ];

  viewDetails(v: any): void {
    alert(`Verstoß-Details:\nQuelle: ${v.source_ref}\nGrund: ${v.reason_code}\nZiel: ${v.worker_kind}\nLLM Scope: ${v.model_scope}`);
  }
}
