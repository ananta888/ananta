import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessRule, Sensitivity } from '../../models/context-access-policy.model';

@Component({
  selector: 'app-rule-list',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="rule-list-container">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h6>Regeln (Precedence)</h6>
        <button class="btn btn-sm btn-outline-success" (click)="addRule()">+ Regel hinzufügen</button>
      </div>

      <div class="list-group">
        <div *ngFor="let rule of rules; let i = index" 
             class="list-group-item list-group-item-action d-flex align-items-center gap-3 p-3">
          <div class="handle text-muted" title="Drag to reorder">
            <i class="bi bi-grip-vertical"></i>
          </div>
          
          <div class="flex-grow-1">
            <div class="d-flex justify-content-between align-items-center">
              <span class="fw-bold">{{ rule.id || 'Unbenannte Regel' }}</span>
              <span class="badge" [ngClass]="getSensitivityBadgeClass(rule.sensitivity)">
                {{ rule.sensitivity }}
              </span>
            </div>
            <p class="mb-1 small text-secondary">{{ rule.description || 'Keine Beschreibung' }}</p>
            <div class="d-flex gap-2 mt-1">
              <span class="badge rounded-pill bg-light text-dark border small" *ngIf="rule.read_allowed">Read</span>
              <span class="badge rounded-pill bg-light text-dark border small" *ngIf="rule.write_allowed">Write</span>
              <span class="badge rounded-pill bg-light text-dark border small" *ngIf="rule.send_allowed">Send</span>
              <span class="badge rounded-pill bg-info text-white small" *ngIf="rule.redaction_required" title="Schwärzung erforderlich">
                <i class="bi bi-shield-lock me-1"></i>Redact
              </span>
              <span class="badge rounded-pill bg-info text-white small" *ngIf="rule.summarization_allowed" title="Zusammenfassung erlaubt">
                <i class="bi bi-file-earmark-zip me-1"></i>Summary
              </span>
            </div>
          </div>

          <div class="actions">
            <button class="btn btn-sm btn-link" (click)="editRule(rule)">Edit</button>
            <button class="btn btn-sm btn-link text-danger" (click)="removeRule(i)">Del</button>
          </div>
        </div>

        <div *ngIf="rules.length === 0" class="list-group-item text-center py-4 text-muted">
          Keine Regeln definiert.
        </div>
      </div>
    </div>
  `,
  styles: [`
    .handle { cursor: grab; font-size: 1.2rem; }
    .gap-3 { gap: 1rem; }
  `]
})
export class RuleListComponent {
  @Input() rules: ContextAccessRule[] = [];
  @Output() ruleAdded = new EventEmitter<void>();
  @Output() ruleEdited = new EventEmitter<ContextAccessRule>();
  @Output() rulesChanged = new EventEmitter<ContextAccessRule[]>();

  addRule(): void {
    this.ruleAdded.emit();
  }

  editRule(rule: ContextAccessRule): void {
    this.ruleEdited.emit(rule);
  }

  removeRule(index: number): void {
    if (confirm('Regel wirklich löschen?')) {
      const newRules = [...this.rules];
      newRules.splice(index, 1);
      this.rulesChanged.emit(newRules);
    }
  }

  getSensitivityBadgeClass(s?: Sensitivity): string {
    switch (s) {
      case Sensitivity.public: return 'bg-success';
      case Sensitivity.secret:
      case Sensitivity.credential: return 'bg-danger';
      case Sensitivity.project_internal: return 'bg-info text-dark';
      default: return 'bg-secondary';
    }
  }
}
