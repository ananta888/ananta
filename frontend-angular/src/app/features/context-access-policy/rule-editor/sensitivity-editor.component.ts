import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Sensitivity } from '../../../models/context-access-policy.model';

@Component({
  selector: 'app-sensitivity-editor',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="sensitivity-editor card shadow-sm mb-3">
      <div class="card-header bg-light">
        <h6 class="mb-0">Sensitivität & Klassifizierung</h6>
      </div>
      <div class="card-body">
        <div class="row g-2">
          <div *ngFor="let s of sensitivityOptions" class="col-md-4">
            <button class="btn btn-outline-secondary w-100 text-start d-flex flex-column p-2"
                    [class.active]="selectedSensitivity === s.value"
                    [class.btn-outline-danger]="isHighRisk(s.value)"
                    (click)="select(s.value)">
              <span class="fw-bold">{{ s.label }}</span>
              <small class="text-muted">{{ s.description }}</small>
            </button>
          </div>
        </div>

        <div *ngIf="isHighRisk(selectedSensitivity)" class="alert alert-danger mt-3 mb-0">
          <i class="bi bi-exclamatation-triangle-fill me-2"></i>
          <strong>Hochrisiko-Daten:</strong> Cloud-Versand und externe Worker sollten für diese Klassifizierung deaktiviert werden.
        </div>
      </div>
    </div>
  `,
  styles: [`
    .btn-outline-secondary.active { background-color: #e9ecef; border-color: #adb5bd; color: #212529; }
    .btn-outline-danger.active { background-color: #f8d7da; border-color: #f5c2c7; color: #842029; }
  `]
})
export class SensitivityEditorComponent {
  @Input() selectedSensitivity: Sensitivity = Sensitivity.unknown;
  @Output() changed = new EventEmitter<Sensitivity>();

  sensitivityOptions = [
    { value: Sensitivity.public, label: 'Öffentlich', description: 'Daten ohne Einschränkungen.' },
    { value: Sensitivity.project_internal, label: 'Projekt-Intern', description: 'Nur für Teammitglieder.' },
    { value: Sensitivity.customer_confidential, label: 'Kunden-Vertraulich', description: 'Spezifische Kundendaten.' },
    { value: Sensitivity.security_sensitive, label: 'Sicherheits-Relevant', description: 'Infrastruktur & Security.' },
    { value: Sensitivity.secret, label: 'Geheimnis (Secret)', description: 'Keys, Zertifikate.' },
    { value: Sensitivity.credential, label: 'Zugangsdaten', description: 'Passwörter, Tokens.' },
    { value: Sensitivity.regulated_data, label: 'Reguliert (DSGVO)', description: 'Personenbezogene Daten.' }
  ];

  isHighRisk(s: Sensitivity): boolean {
    return [Sensitivity.secret, Sensitivity.credential, Sensitivity.regulated_data].includes(s);
  }

  select(s: Sensitivity): void {
    this.selectedSensitivity = s;
    this.changed.emit(s);
  }
}
