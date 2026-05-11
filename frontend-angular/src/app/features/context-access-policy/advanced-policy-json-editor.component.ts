import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-advanced-policy-json-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="json-editor-container card shadow-sm h-100">
      <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
        <h6 class="mb-0">Advanced JSON Editor</h6>
        <span class="badge bg-warning text-dark">Experten-Modus</span>
      </div>
      <div class="card-body p-0 d-flex flex-column">
        <textarea class="form-control flex-grow-1 font-monospace p-3" 
                  [(ngModel)]="jsonContent" 
                  (ngModelChange)="onContentChange()"
                  placeholder='{ "rules": [...] }'></textarea>
        
        <div *ngIf="parseError" class="alert alert-danger m-2 p-2 small">
          <strong>Parse-Fehler:</strong> {{ parseError }}
        </div>
      </div>
      <div class="card-footer d-flex justify-content-between">
        <small class="text-muted">Backend-Schema wird beim Speichern validiert.</small>
        <button class="btn btn-sm btn-primary" [disabled]="!!parseError" (click)="save()">Speichern</button>
      </div>
    </div>
  `,
  styles: [`
    .json-editor-container { min-height: 400px; }
    textarea { border: none; resize: none; border-radius: 0; background-color: #f8f9fa; }
    textarea:focus { box-shadow: none; background-color: #fff; }
  `]
})
export class AdvancedPolicyJsonEditorComponent {
  @Input() set policy(value: any) {
    this.jsonContent = JSON.stringify(value, null, 2);
    this.parseError = null;
  }
  @Output() saved = new EventEmitter<any>();

  jsonContent = '';
  parseError: string | null = null;

  onContentChange(): void {
    try {
      JSON.parse(this.jsonContent);
      this.parseError = null;
    } catch (e: any) {
      this.parseError = e.message;
    }
  }

  save(): void {
    if (!this.parseError) {
      try {
        const parsed = JSON.parse(this.jsonContent);
        this.saved.emit(parsed);
      } catch (e: any) {
        this.parseError = e.message;
      }
    }
  }
}
