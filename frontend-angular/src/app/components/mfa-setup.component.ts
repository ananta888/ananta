import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { UserAuthService } from '../services/user-auth.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-mfa-setup',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h3>Multi-Faktor-Authentifizierung (MFA)</h3>
      <div *ngIf="!setupData && !mfaEnabled">
        <p>Erhöhen Sie die Sicherheit Ihres Kontos durch MFA.</p>
        <button (click)="startSetup()">MFA Einrichten</button>
      </div>

      <div *ngIf="setupData" class="setup-container">
        <p>1. Scannen Sie diesen QR-Code mit einer Authentifikator-App (z.B. Google Authenticator, Authy):</p>
        <div class="qr-code">
          <img [src]="setupData.qr_code" alt="QR Code">
        </div>
        <p>Oder geben Sie den Code manuell ein: <code>{{setupData.secret}}</code></p>
        
        <p>2. Geben Sie den 6-stelligen Code aus der App ein:</p>
        <div class="row">
          <input [(ngModel)]="token" placeholder="000000" maxlength="6" style="width: 100px; text-align: center; font-size: 20px;">
          <button (click)="verify()">Aktivieren</button>
          <button (click)="setupData = null" class="button-outline">Abbrechen</button>
        </div>
      </div>

      <div *ngIf="mfaEnabled && !setupData">
        <p class="status-success">✅ MFA ist für Ihr Konto aktiviert.</p>
        <button (click)="disable()" class="button-outline danger">MFA Deaktivieren</button>
      </div>
    </div>
  `,
  styles: [`
    .qr-code { background: white; padding: 10px; display: inline-block; margin: 10px 0; }
    .status-success { color: #28a745; font-weight: bold; }
    .setup-container { margin-top: 15px; }
  `]
})
export class MfaSetupComponent {
  setupData: any = null;
  token = '';
  mfaEnabled = false;

  constructor(private auth: UserAuthService, private ns: NotificationService) {
    this.auth.user$.subscribe(user => {
      // In einem echten Szenario müsste der User-Status vom Backend geladen werden,
      // da der JWT-Payload evtl. veraltet ist. Aber für dieses Projekt nehmen wir an,
      // dass wir den Status kennen oder später aktualisieren.
      // Der UserAuthService könnte eine getProfile() Methode haben.
    });
  }

  startSetup() {
    this.auth.mfaSetup().subscribe({
      next: data => this.setupData = data,
      error: err => this.ns.error(err.error?.error || 'Setup konnte nicht gestartet werden')
    });
  }

  verify() {
    this.auth.mfaVerify(this.token).subscribe({
      next: () => {
        this.ns.success('MFA erfolgreich aktiviert');
        this.setupData = null;
        this.mfaEnabled = true;
      },
      error: err => this.ns.error(err.error?.error || 'Verifizierung fehlgeschlagen')
    });
  }

  disable() {
    if (confirm('MFA wirklich deaktivieren?')) {
      this.auth.mfaDisable().subscribe({
        next: () => {
          this.ns.success('MFA deaktiviert');
          this.mfaEnabled = false;
        },
        error: err => this.ns.error(err.error?.error || 'Deaktivierung fehlgeschlagen')
      });
    }
  }
}
