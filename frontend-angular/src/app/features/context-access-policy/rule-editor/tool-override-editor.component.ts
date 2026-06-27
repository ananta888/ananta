import { Component, EventEmitter, Input, Output } from '@angular/core';

import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-tool-override-editor',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="tool-overrides mt-3">
      <label class="form-label fw-bold small text-muted">Tool-spezifische Overrides (T017)</label>
      <div class="list-group list-group-flush border rounded">
        @for (toolId of toolIds; track toolId) {
          <div class="list-group-item d-flex justify-content-between align-items-center py-1 px-2">
            <span class="small font-monospace">{{ toolId }}</span>
            <div class="d-flex gap-2 align-items-center">
              <div class="form-check form-switch mb-0">
                <input class="form-check-input" type="checkbox"
                  [checked]="overrides[toolId]?.write_allowed === false"
                  (change)="toggleDenyWrite(toolId)">
                <label class="form-check-label small">Deny Write</label>
              </div>
              <button class="btn btn-sm btn-link text-danger p-0" (click)="removeOverride(toolId)">
                <i class="bi bi-x"></i>
              </button>
            </div>
          </div>
        }
        <div class="list-group-item p-2 bg-light">
          <div class="input-group input-group-sm">
            <input type="text" class="form-control" placeholder="Tool ID (z.B. shell)" [(ngModel)]="newToolId">
            <button class="btn btn-outline-secondary" type="button" (click)="addOverride()">Hinzufügen</button>
          </div>
        </div>
      </div>
    </div>
    `,
  styles: [`
    .tool-overrides { font-size: 0.85rem; }
  `]
})
export class ToolOverrideEditorComponent {
  @Input() overrides: { [toolId: string]: any } = {};
  @Output() changed = new EventEmitter<any>();

  newToolId = '';

  get toolIds(): string[] {
    return Object.keys(this.overrides);
  }

  addOverride(): void {
    if (this.newToolId && !this.overrides[this.newToolId]) {
      this.overrides[this.newToolId] = { write_allowed: false };
      this.newToolId = '';
      this.emit();
    }
  }

  removeOverride(toolId: string): void {
    delete this.overrides[toolId];
    this.emit();
  }

  toggleDenyWrite(toolId: string): void {
    const current = this.overrides[toolId] || {};
    this.overrides[toolId] = { 
      ...current, 
      write_allowed: current.write_allowed === false ? undefined : false 
    };
    this.emit();
  }

  emit(): void {
    this.changed.emit(this.overrides);
  }
}
