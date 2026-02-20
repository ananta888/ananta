import { Component, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { finalize, timeout } from 'rxjs';
import { UserAuthService } from '../services/user-auth.service';
import { AgentDirectoryService } from '../services/agent-directory.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="login-container">
      <div class="card" style="max-width: 400px; margin: 100px auto;">
        <h2>Ananta Login</h2>
        <form (submit)="onLogin($event)" aria-label="Login-Formular">
          <div class="form-group">
            <label for="username">Benutzername</label>
            <input
              type="text"
              id="username"
              [(ngModel)]="username"
              name="username"
              required
              [attr.aria-describedby]="error ? 'login-error' : (getUsernameError() ? 'username-error' : null)"
              [attr.aria-invalid]="getUsernameError() ? 'true' : null"
              aria-required="true"
              (blur)="usernameTouched = true">
            @if (usernameTouched && getUsernameError()) {
              <small id="username-error" class="error-msg" style="display: block; margin-top: 4px;">{{getUsernameError()}}</small>
            }
          </div>
          @if (!mfaRequired) {
            <div class="form-group">
              <label for="password">Passwort</label>
              <input
                type="password"
                id="password"
                [(ngModel)]="password"
                name="password"
                required
                [attr.aria-describedby]="error ? 'login-error' : (getPasswordError() ? 'password-error' : null)"
                [attr.aria-invalid]="getPasswordError() ? 'true' : null"
                aria-required="true"
                (blur)="passwordTouched = true">
              @if (passwordTouched && getPasswordError()) {
                <small id="password-error" class="error-msg" style="display: block; margin-top: 4px;">{{getPasswordError()}}</small>
              }
            </div>
          }
          @if (mfaRequired) {
            <div class="form-group">
              <label for="mfaToken">MFA Code / Backup Code</label>
              <input
                type="text"
                id="mfaToken"
                [(ngModel)]="mfaToken"
                name="mfaToken"
                placeholder="000000 oder Backup-Code"
                required
                autoFocus
                [attr.aria-describedby]="getMfaError() ? 'mfa-error' : 'mfa-help'"
                [attr.aria-invalid]="getMfaError() ? 'true' : null"
                aria-required="true"
                (blur)="mfaTouched = true">
              @if (mfaTouched && getMfaError()) {
                <small id="mfa-error" class="error-msg" style="display: block; margin-top: 4px;">{{getMfaError()}}</small>
              }
              <p id="mfa-help" class="muted" style="font-size: 11px; margin-top: 4px;">Bitte geben Sie den Code aus Ihrer App oder einen Backup-Code ein.</p>
            </div>
          }
          @if (error) {
            <div id="login-error" class="error-msg" role="alert" aria-live="polite">{{error}}</div>
          }
          <button
            type="submit"
            [disabled]="loading"
            class="primary"
            style="width: 100%; margin-top: 16px;"
            [attr.aria-label]="loading ? 'Lädt' : (mfaRequired ? 'MFA-Code verifizieren' : 'Anmelden')">
            {{ loading ? 'Lade...' : (mfaRequired ? 'Verifizieren' : 'Anmelden') }}
          </button>
          @if (!mfaRequired) {
            <div style="text-align: center; margin-top: 12px;">
              <a
                href="#"
                (click)="onForgotPassword($event)"
                (keydown.enter)="onForgotPassword($event)"
                (keydown.space)="onForgotPassword($event)"
                style="font-size: 14px; color: #0066cc; text-decoration: none;"
                role="button"
                tabindex="0"
                aria-label="Passwort vergessen">
                Passwort vergessen?
              </a>
            </div>
          }
          @if (mfaRequired) {
            <button
              type="button"
              (click)="mfaRequired = false; error = ''"
              class="button-outline"
              style="width: 100%; margin-top: 8px;"
              aria-label="Zurück zur Passwort-Eingabe">
              Zurück zum Passwort
            </button>
          }
        </form>
      </div>
    </div>
    `,
  styles: [`
    .login-container { height: 100vh; background: #f5f5f5; display: flex; align-items: flex-start; }
    .error-msg { color: #dc3545; margin-top: 8px; font-size: 14px; }
    .form-group { margin-bottom: 12px; }
    label { display: block; margin-bottom: 4px; font-weight: 500; }
    input { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }
  `]
})
export class LoginComponent {
  private http = inject(HttpClient);
  private router = inject(Router);
  private auth = inject(UserAuthService);
  private dir = inject(AgentDirectoryService);

  username = '';
  password = '';
  mfaToken = '';
  mfaRequired = false;
  loading = false;
  error = '';
  usernameTouched = false;
  passwordTouched = false;
  mfaTouched = false;

  getUsernameError(): string | null {
    if (!this.username || this.username.trim().length === 0) {
      return 'Benutzername ist erforderlich';
    }
    return null;
  }

  getPasswordError(): string | null {
    if (!this.password || this.password.length === 0) {
      return 'Passwort ist erforderlich';
    }
    return null;
  }

  getMfaError(): string | null {
    if (!this.mfaToken || this.mfaToken.trim().length === 0) {
      return 'MFA-Code ist erforderlich';
    }
    if (this.mfaToken.length < 6) {
      return 'MFA-Code muss mindestens 6 Zeichen haben';
    }
    return null;
  }

  onForgotPassword(e: Event) {
    e.preventDefault();
    alert('Passwort-Reset-Funktion ist noch nicht implementiert. Bitte kontaktieren Sie Ihren Administrator.');
  }

  onLogin(e: Event) {
    e.preventDefault();
    this.loading = true;
    this.error = '';

    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.error = 'Kein Hub in den Einstellungen gefunden.';
      this.loading = false;
      return;
    }

    const body: any = {
      username: this.username,
      password: this.password
    };
    if (this.mfaRequired) {
      body.mfa_token = this.mfaToken;
    }

    this.http.post<any>(`${hub.url}/login`, body)
      .pipe(
        timeout(10000),
        finalize(() => {
          this.loading = false;
        })
      )
      .subscribe({
        next: res => {
        const payload = res?.data ?? res;
        const requiresMfa = res?.status === 'mfa_required' || (payload?.mfa_required === true && !payload?.access_token);

        if (requiresMfa) {
          this.mfaRequired = true;
          this.mfaToken = '';
          return;
        }

        const accessToken = payload?.access_token ?? null;
        if (!accessToken) {
          this.error = res?.message || payload?.message || payload?.error || 'Login fehlgeschlagen';
          return;
        }

        this.auth.setTokens(accessToken, payload?.refresh_token ?? null);
        this.router.navigate(['/dashboard']);
        },
        error: err => {
          this.error = err?.error?.message || err?.error?.error || err?.error?.detail || 'Login fehlgeschlagen';
        }
      });
  }
}
