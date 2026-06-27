import { Component, EventEmitter, Input, Output } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { ToolOverrideEditorComponent } from './tool-override-editor.component';

@Component({
  selector: 'app-access-permission-editor',
  standalone: true,
  imports: [FormsModule, ToolOverrideEditorComponent],
  template: `
    <div class="permission-editor card shadow-sm mb-3">
      <div class="card-header bg-light">
        <h6 class="mb-0">Zugriffsberechtigungen</h6>
      </div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-4">
            <div class="form-check form-switch p-2 border rounded" [class.bg-light]="read_allowed">
              <input class="form-check-input ms-0 me-2" type="checkbox" id="read_allowed" [(ngModel)]="read_allowed" (change)="emit()">
              <label class="form-check-label fw-bold" for="read_allowed">Lesen erlaubt</label>
              <p class="small text-muted mb-0">Worker darf Daten lesen.</p>
            </div>
          </div>
          <div class="col-md-4">
            <div class="form-check form-switch p-2 border rounded" [class.bg-light]="send_allowed">
              <input class="form-check-input ms-0 me-2" type="checkbox" id="send_allowed" [(ngModel)]="send_allowed" (change)="emit()">
              <label class="form-check-label fw-bold" for="send_allowed">An LLM senden</label>
              <p class="small text-muted mb-0">Daten dürfen an Modelle/Cloud.</p>
            </div>
          </div>
          <div class="col-md-4">
            <div class="form-check form-switch p-2 border rounded" [class.bg-light]="write_allowed" [class.border-warning]="write_allowed">
              <input class="form-check-input ms-0 me-2" type="checkbox" id="write_allowed" [(ngModel)]="write_allowed" (change)="emit()">
              <label class="form-check-label fw-bold" for="write_allowed">Schreiben erlaubt</label>
              @if (write_allowed) {
                <p class="small text-muted mb-0 text-danger">Gefahr: Modifikation möglich!</p>
              }
              @if (!write_allowed) {
                <p class="small text-muted mb-0">Nur Lesezugriff.</p>
              }
            </div>
          </div>
        </div>
    
        <div class="mt-3 row g-3">
          <div class="col-md-6">
            <div class="form-check form-switch p-2 border rounded">
              <input class="form-check-input ms-0 me-2" type="checkbox" id="cloud_allowed" [(ngModel)]="cloud_allowed" (change)="emit()">
              <label class="form-check-label fw-bold" for="cloud_allowed">Cloud erlaubt</label>
            </div>
          </div>
          <div class="col-md-6">
            <div class="form-check form-switch p-2 border rounded">
              <input class="form-check-input ms-0 me-2" type="checkbox" id="external_worker_allowed" [(ngModel)]="external_worker_allowed" (change)="emit()">
              <label class="form-check-label fw-bold" for="external_worker_allowed">Externe Worker erlaubt</label>
            </div>
          </div>
        </div>
    
        @if (write_allowed) {
          <div class="mt-3 p-2 bg-light rounded">
            <div class="form-check">
              <input class="form-check-input" type="checkbox" id="approval_required" [(ngModel)]="approval_required" (change)="emit()">
              <label class="form-check-label" for="approval_required">
                Manuelle Freigabe für Schreibzugriff erforderlich
              </label>
            </div>
          </div>
        }
    
        <!-- T017: Tool Overrides -->
        <app-tool-override-editor
          [overrides]="tool_overrides"
          (changed)="onToolOverridesChanged($event)">
        </app-tool-override-editor>
      </div>
    </div>
    `,
  styles: [`
    .form-switch .form-check-input { width: 2.5em; height: 1.25em; }
  `]
})
export class AccessPermissionEditorComponent {
  @Input() read_allowed = false;
  @Input() send_allowed = false;
  @Input() write_allowed = false;
  @Input() cloud_allowed = false;
  @Input() external_worker_allowed = false;
  @Input() approval_required = true;
  @Input() tool_overrides: { [toolId: string]: any } = {};

  @Output() changed = new EventEmitter<any>();

  onToolOverridesChanged(overrides: any): void {
    this.tool_overrides = overrides;
    this.emit();
  }

  emit(): void {
    this.changed.emit({
      read_allowed: this.read_allowed,
      send_allowed: this.send_allowed,
      write_allowed: this.write_allowed,
      cloud_allowed: this.cloud_allowed,
      external_worker_allowed: this.external_worker_allowed,
      approval_required: this.approval_required,
      tool_overrides: this.tool_overrides
    });
  }
}
