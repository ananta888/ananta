import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';
import { ContextBlockAccessDecision, DestinationContextPreview, ModelScope } from '../../models/context-access-policy.model';

@Component({
  selector: 'app-preview-decision',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="preview-decision card shadow-sm">
      <div class="card-header">
        <h6 class="mb-0">Entscheidungs-Vorschau (Preview)</h6>
      </div>
      <div class="card-body">
        <div class="mb-3">
          <label class="form-label">Quell-Metadaten (Simuliert)</label>
          <textarea class="form-control form-control-sm font-monospace" [(ngModel)]="sourceMetadataJson" rows="3"></textarea>
        </div>
    
        <div class="row g-2 mb-3">
          <div class="col-md-6">
            <label class="form-label small">Worker Kind</label>
            <input type="text" class="form-control form-control-sm" [(ngModel)]="destination.worker_kind">
          </div>
          <div class="col-md-6">
            <label class="form-label small">Model Scope</label>
            <select class="form-select form-select-sm" [(ngModel)]="destination.model_scope">
              @for (s of modelScopes; track s) {
                <option [value]="s">{{ s }}</option>
              }
            </select>
          </div>
        </div>
    
        <button class="btn btn-sm btn-info w-100" (click)="preview()">Entscheidung prüfen</button>
    
        @if (decision) {
          <div class="mt-3 p-2 rounded" [ngClass]="getDecisionClass(decision.decision)">
            <div class="d-flex justify-content-between">
              <strong>{{ decision.decision | uppercase }}</strong>
              <small>{{ decision.reason_code }}</small>
            </div>
            @if (decision.reason_detail) {
              <p class="mb-0 small">{{ decision.reason_detail }}</p>
            }
          </div>
        }
      </div>
    </div>
    `,
  styles: []
})
export class PreviewDecisionComponent {
  private api = inject(ContextAccessPolicyApiService);

  @Input() baseUrl = '';
  
  sourceMetadataJson = '{\n  "path": "src/app/app.component.ts",\n  "type": "local_file",\n  "sensitivity": "project_internal"\n}';
  
  destination: DestinationContextPreview = {
    worker_kind: 'native_ananta_worker',
    runtime_kind: 'local_process',
    model_scope: ModelScope.local_model,
    cloud_effective: false,
    external_effective: false
  };

  modelScopes = Object.values(ModelScope);
  decision?: ContextBlockAccessDecision;

  preview(): void {
    try {
      const source_metadata = JSON.parse(this.sourceMetadataJson);
      this.api.previewDecision(this.baseUrl, { source_metadata, destination: this.destination }).subscribe(res => {
        this.decision = res;
      });
    } catch (e: any) {
      alert('Ungültiges JSON in Quell-Metadaten: ' + e.message);
    }
  }

  getDecisionClass(d: string): string {
    switch (d) {
      case 'allow': return 'bg-success text-white';
      case 'deny': return 'bg-danger text-white';
      case 'approval_required': return 'bg-warning text-dark';
      default: return 'bg-light border';
    }
  }
}
