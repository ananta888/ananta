import { Component, OnInit, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { UserAuthService } from '../services/user-auth.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-mfa-setup',
  imports: [FormsModule],
  template: `
    <div class="card">
      <h3>Multi-Faktor-Authentifizierung (MFA)</h3>
      @if (!setupData && !mfaEnabled) {
        <div>
          <p>Erhöhen Sie die Sicherheit Ihres Kontos durch MFA.</p>
          <button (click)="startSetup()">MFA Einrichten</button>
        </div>
      }
    
      @if (setupData) {
        <div class="setup-container">
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
      }
    
      @if (backupCodes.length > 0) {
        <div class="card success" style="margin-top: 15px;">
          <h4>⚠️ MFA Backup-Codes</h4>
          <p>Bitte speichern Sie diese Codes an einem sicheren Ort. Sie können verwendet werden, wenn Sie den Zugriff auf Ihre App verlieren.</p>
          <div class="grid cols-2" style="background: #f8f9fa; padding: 10px; border-radius: 4px; font-family: monospace;">
            @for (code of backupCodes; track code) {
              <div>{{code}}</div>
            }
          </div>
          <button (click)="backupCodes = []" style="margin-top: 10px;">Ich habe die Codes gespeichert</button>
        </div>
      }
    
      @if (mfaEnabled && !setupData && backupCodes.length === 0) {
        <div>
          <p class="status-success">✅ MFA ist für Ihr Konto aktiviert.</p>
          <button (click)="disable()" class="button-outline danger">MFA Deaktivieren</button>
        </div>
      }
    </div>
    `,
  styles: [`
    .qr-code { background: white; padding: 10px; display: inline-block; margin: 10px 0; }
    .status-success { color: #28a745; font-weight: bold; }
    .setup-container { margin-top: 15px; }
  `]
})
export class MfaSetupComponent implements OnInit {
  private auth = inject(UserAuthService);
  private ns = inject(NotificationService);

  setupData: any = null;
  token = '';
  mfaEnabled = false;
  backupCodes: string[] = [];

  constructor() {
    this.auth.user$.subscribe(user => {
      // In einem echten Szenario müsste der User-Status vom Backend geladen werden,
      // da der JWT-Payload evtl. veraltet ist. Aber für dieses Projekt nehmen wir an,
      // dass wir den Status kennen oder später aktualisieren.
      // Der UserAuthService könnte eine getProfile() Methode haben.
    });
  }

  ngOnInit() {
    this.loadMfaStatus();
  }

  private loadMfaStatus() {
    this.auth.getMe().subscribe({
      next: (profile: any) => {
        this.mfaEnabled = Boolean(profile?.mfa_enabled);
      },
      error: () => {
        this.mfaEnabled = false;
      }
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
      next: (res: any) => {
        this.ns.success('MFA erfolgreich aktiviert');
        this.setupData = null;
        this.mfaEnabled = true;
        if (res?.access_token) {
          this.auth.setTokens(res.access_token);
        }
        if (res.backup_codes) {
          this.backupCodes = res.backup_codes;
        }
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
          this.loadMfaStatus();
        },
        error: err => this.ns.error(err.error?.error || 'Deaktivierung fehlgeschlagen')
      });
    }
  }
}
