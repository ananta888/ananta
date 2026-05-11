import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SourceType } from '../../../models/context-access-policy.model';

@Component({
  selector: 'app-source-match-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="source-match-editor card shadow-sm mb-3">
      <div class="card-header bg-light">
        <h6 class="mb-0">Quellen-Filter (Source Match)</h6>
      </div>
      <div class="card-body">
        <div class="mb-3">
          <label class="form-label">Pfad-Muster (Glob Pattern)</label>
          <div class="input-group">
            <input type="text" class="form-control" [(ngModel)]="pattern" placeholder="z.B. src/**/*.ts">
            <button class="btn btn-outline-secondary" type="button" (click)="addPresetPattern('**/*.env')">.env</button>
            <button class="btn btn-outline-secondary" type="button" (click)="addPresetPattern('**/secrets/**')">Secrets</button>
          </div>
          <small class="text-muted">Nutzen Sie Glob-Syntax wie ** für alle Verzeichnisse.</small>
        </div>

        <div class="mb-3">
          <label class="form-label">Quellentypen</label>
          <div class="d-flex flex-wrap gap-2">
            <div *ngFor="let type of availableSourceTypes" class="form-check">
              <input class="form-check-input" type="checkbox" 
                     [id]="'type-' + type"
                     [checked]="selectedTypes.includes(type)"
                     (change)="toggleType(type)">
              <label class="form-check-label" [for]="'type-' + type">
                {{ type }}
              </label>
            </div>
          </div>
        </div>

        <div class="d-flex justify-content-end">
          <button class="btn btn-sm btn-primary" (click)="save()">Übernehmen</button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .gap-2 { gap: 0.5rem; }
  `]
})
export class SourceMatchEditorComponent {
  @Input() pattern = '';
  @Input() selectedTypes: SourceType[] = [];
  @Output() changed = new EventEmitter<{ pattern: string, types: SourceType[] }>();

  availableSourceTypes = Object.values(SourceType);

  addPresetPattern(p: string): void {
    this.pattern = p;
  }

  toggleType(type: SourceType): void {
    if (this.selectedTypes.includes(type)) {
      this.selectedTypes = this.selectedTypes.filter(t => t !== type);
    } else {
      this.selectedTypes = [...this.selectedTypes, type];
    }
  }

  save(): void {
    this.changed.emit({ pattern: this.pattern, types: this.selectedTypes });
  }
}
