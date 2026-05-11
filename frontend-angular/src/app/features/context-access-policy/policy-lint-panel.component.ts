import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PolicyLintResult, PolicyLintItem } from '../../models/context-access-policy.model';

@Component({
  selector: 'app-policy-lint-panel',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="lint-panel card shadow-sm" *ngIf="lintResult">
      <div class="card-header d-flex justify-content-between align-items-center" 
           [ngClass]="lintResult.is_valid ? 'bg-success text-white' : 'bg-danger text-white'">
        <h6 class="mb-0">Prüfergebnis (Lint)</h6>
        <span class="badge rounded-pill bg-light text-dark">
          {{ lintResult.is_valid ? 'Gültig' : 'Ungültig' }}
        </span>
      </div>
      <div class="card-body p-0">
        <ul class="list-group list-group-flush">
          <li *ngFor="let item of allItems" class="list-group-item">
            <div class="d-flex w-100 justify-content-between">
              <span class="badge me-2" [ngClass]="getSeverityClass(item.severity)">
                {{ item.severity | uppercase }}
              </span>
              <small class="text-muted" *ngIf="item.rule_id">Regel: {{ item.rule_id }}</small>
            </div>
            <p class="mt-2 mb-1">{{ item.message }}</p>
            <div *ngIf="item.suggested_fix" class="mt-2 p-2 bg-light border rounded small">
              <strong>Vorschlag:</strong> {{ item.suggested_fix }}
            </div>
          </li>
          <li *ngIf="allItems.length === 0" class="list-group-item text-center py-3 text-muted">
            Keine Probleme gefunden.
          </li>
        </ul>
      </div>
    </div>
  `,
  styles: [`
    .lint-panel { max-height: 400px; overflow-y: auto; }
    .badge-info { background-color: #17a2b8; }
    .badge-warning { background-color: #ffc107; color: #212529; }
    .badge-error { background-color: #dc3545; }
  `]
})
export class PolicyLintPanelComponent {
  @Input() lintResult?: PolicyLintResult;

  get allItems(): PolicyLintItem[] {
    if (!this.lintResult) return [];
    return [...this.lintResult.errors, ...this.lintResult.warnings, ...this.lintResult.infos];
  }

  getSeverityClass(severity: string): string {
    return `badge-${severity}`;
  }
}
