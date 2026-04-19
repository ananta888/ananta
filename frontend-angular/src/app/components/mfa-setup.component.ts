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
          <p>Schuetze dein Konto mit einem zweiten Login-Schritt.</p>
          <button (click)="startSetup()" aria-label="MFA Einrichtung starten">MFA einrichten</button>
        </div>
      }

      @if (setupData) {
        <div class="setup-container">
          <p id="mfa-step1">1. Scanne den QR-Code mit deiner Authenticator-App.</p>
          <div class="qr-code">
            <img [src]="setupData.qr_code" alt="QR Code für MFA Einrichtung">
          </div>
          <p>Oder gib diesen Code manuell ein: <code aria-label="Geheimer Schlüssel">{{setupData.secret}}</code></p>
          <p id="mfa-step2">2. Gib den 6-stelligen Code aus der App ein.</p>
          <div class="row">
            <label for="mfa-token" class="sr-only">Verifizierungscode</label>
            <input
              id="mfa-token"
              [(ngModel)]="token"
              placeholder="000000"
              maxlength="6"
              class="mfa-token-input"
              aria-describedby="mfa-step2"
              aria-required="true"
              inputmode="numeric"
              pattern="[0-9]*">
            <button (click)="verify()" [disabled]="token.length !== 6" aria-label="MFA Code verifizieren und aktivieren">Aktivieren</button>
            <button (click)="setupData = null" class="button-outline" aria-label="MFA Einrichtung abbrechen">Abbrechen</button>
          </div>
        </div>
      }

      @if (backupCodes.length > 0) {
        <div class="card success mfa-backup-section" role="alert" aria-live="polite">
          <h4>MFA Backup-Codes</h4>
          <p>Speichere diese Codes sicher. Du brauchst sie, falls du den Zugriff auf deine App verlierst.</p>
          <div
            class="grid cols-2 backup-codes-display"
            role="list"
            aria-label="MFA Backup-Codes">
            @for (code of backupCodes; track code) {
              <div role="listitem">{{code}}</div>
            }
          </div>
          <button (click)="backupCodes = []" class="mfa-confirm-btn" aria-label="Bestätigen dass Backup-Codes gespeichert wurden">Ich habe die Codes gespeichert</button>
        </div>
      }

      @if (mfaEnabled && !setupData && backupCodes.length === 0) {
        <div>
          <p class="status-success" role="status">MFA ist fuer dein Konto aktiviert.</p>
          <button (click)="disable()" class="button-outline danger" aria-label="MFA fuer dieses Konto deaktivieren">MFA deaktivieren</button>
        </div>
      }
    </div>
    `
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
