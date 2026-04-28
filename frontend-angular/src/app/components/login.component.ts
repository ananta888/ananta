import { Component, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Capacitor } from '@capacitor/core';
import { firstValueFrom, timeout } from 'rxjs';
import { UserAuthService } from '../services/user-auth.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { PythonRuntimeService } from '../services/python-runtime.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="login-container">
      <div class="card login-card">
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
              <small id="username-error" class="error-msg error-msg-block">{{getUsernameError()}}</small>
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
                <small id="password-error" class="error-msg error-msg-block">{{getPasswordError()}}</small>
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
                <small id="mfa-error" class="error-msg error-msg-block">{{getMfaError()}}</small>
              }
              <p id="mfa-help" class="muted mfa-hint">Bitte geben Sie den Code aus Ihrer App oder einen Backup-Code ein.</p>
            </div>
          }
          @if (error) {
            <div id="login-error" class="error-msg" role="alert" aria-live="polite">{{error}}</div>
          }
          <button
            type="submit"
            [disabled]="loading"
            class="primary btn-full btn-mt-md"
            [attr.aria-label]="loading ? 'Lädt' : (mfaRequired ? 'MFA-Code verifizieren' : 'Anmelden')">
            {{ loading ? 'Lade...' : (mfaRequired ? 'Verifizieren' : 'Anmelden') }}
          </button>
          @if (isAndroidNative) {
            <button type="button" class="secondary btn-full btn-mt-sm" (click)="toggleDebugPanel()" [disabled]="debugBusy">
              {{ showDebugPanel ? 'Android Debug ausblenden' : 'Android Debug anzeigen' }}
            </button>
            @if (showDebugPanel) {
              <div class="card card-light mt-sm">
                <div class="row gap-sm wrap">
                  <button type="button" class="secondary" (click)="refreshDebugStatus()" [disabled]="debugBusy">Status aktualisieren</button>
                  <button type="button" class="secondary" (click)="startEmbeddedNow()" [disabled]="debugBusy">Hub/Worker starten</button>
                  <button type="button" class="secondary" (click)="debugLoginProbe()" [disabled]="debugBusy">Login testen</button>
                  <button type="button" class="secondary" (click)="runPluginHealthCheck()" [disabled]="debugBusy">Plugin Health</button>
                </div>
                <pre class="mt-sm">{{ debugText || 'Noch keine Debug-Daten.' }}</pre>
              </div>
            }
          }
          @if (!mfaRequired) {
            <div class="forgot-password">
              <a
                href="#"
                (click)="onForgotPassword($event)"
                (keydown.enter)="onForgotPassword($event)"
                (keydown.space)="onForgotPassword($event)"
                class="link-plain"
                role="button"
                tabindex="0"
                aria-label="Passwort vergessen">
                Passwort vergessen?
              </a>
            </div>
            @if (forgotInfo) {
              <div class="hint-text text-center mt-sm" aria-live="polite">{{ forgotInfo }}</div>
            }
          }
          @if (mfaRequired) {
            <button
              type="button"
              (click)="mfaRequired = false; error = ''"
              class="button-outline btn-full btn-mt-sm"
              aria-label="Zurück zur Passwort-Eingabe">
              Zurück zum Passwort
            </button>
          }
        </form>
      </div>
    </div>
    `
})
export class LoginComponent {
  private http = inject(HttpClient);
  private router = inject(Router);
  private auth = inject(UserAuthService);
  private dir = inject(AgentDirectoryService);
  private pythonRuntime = inject(PythonRuntimeService);

  username = '';
  password = '';
  mfaToken = '';
  mfaRequired = false;
  loading = false;
  error = '';
  forgotInfo = '';
  usernameTouched = false;
  passwordTouched = false;
  mfaTouched = false;
  showDebugPanel = false;
  debugBusy = false;
  debugText = '';

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
    this.forgotInfo = 'Passwort-Reset ist derzeit nicht verfuegbar. Bitte den Administrator kontaktieren.';
  }

  get isAndroidNative(): boolean {
    return this.pythonRuntime.isNative && Capacitor.getPlatform() === 'android';
  }

  async onLogin(e: Event) {
    e.preventDefault();
    this.loading = true;
    this.error = '';
    this.forgotInfo = '';

    try {
      if (this.pythonRuntime.isNative) {
        await this.withTimeout(this.pythonRuntime.ensureEmbeddedControlPlane(), 5000, 'embedded_start_timeout');
      }

      const hub = this.resolveHubForLogin();
      if (!hub) {
        this.error = 'Kein Hub in den Einstellungen gefunden.';
        return;
      }

      const body: any = {
        username: this.username,
        password: this.password
      };
      if (this.mfaRequired) {
        body.mfa_token = this.mfaToken;
      }

      const res = await this.withTimeout(
        firstValueFrom(this.http.post<any>(`${hub.url}/login`, body).pipe(timeout(15000))),
        20000,
        'login_timeout'
      );
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
    } catch (err: any) {
      if (err?.message === 'embedded_start_timeout') {
        this.error = 'Embedded Hub-Start dauert zu lange. Bitte /python-runtime oeffnen und Hub starten.';
        return;
      }
      if (err?.message === 'login_timeout') {
        this.error = 'Login-Request Timeout. Bitte /python-runtime pruefen (Hub/Worker aktiv).';
        return;
      }
      if (Number(err?.status) === 0) {
        this.error = 'Embedded Hub nicht erreichbar. Bitte /python-runtime pruefen und Hub starten.';
      } else {
        this.error = err?.error?.message || err?.error?.error || err?.error?.detail || 'Login fehlgeschlagen';
      }
    } finally {
      this.loading = false;
    }
  }

  private resolveHubForLogin(): { name: string; url: string; role?: 'hub' | 'worker'; token?: string } | null {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) return null;
    const current = String(hub.url || '').trim();
    if (!current) return hub;
    if (!/^https?:\/\/localhost\b/i.test(current)) return hub;
    const normalized = current.replace(/^https?:\/\/localhost\b/i, 'http://127.0.0.1');
    const updated = { ...hub, url: normalized };
    this.dir.upsert(updated);
    return updated;
  }

  private withTimeout<T>(promise: Promise<T>, timeoutMs: number, code: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(code)), timeoutMs);
      promise.then(
        (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        (error) => {
          clearTimeout(timer);
          reject(error);
        }
      );
    });
  }

  async toggleDebugPanel(): Promise<void> {
    this.showDebugPanel = !this.showDebugPanel;
    if (this.showDebugPanel) await this.refreshDebugStatus();
  }

  async refreshDebugStatus(): Promise<void> {
    if (!this.isAndroidNative) return;
    this.debugBusy = true;
    try {
      const hub = this.resolveHubForLogin();
      const runtime = await this.pythonRuntime.getRuntimeStatus();
      let endpointSummary = 'Hub endpoint nicht gesetzt';
      if (hub?.url) {
        try {
          const health = await firstValueFrom(this.http.get<any>(`${hub.url}/health`).pipe(timeout(3000)));
          endpointSummary = `OK ${hub.url}/health -> ${JSON.stringify(health).slice(0, 180)}`;
        } catch (error: any) {
          endpointSummary = `FEHLER ${hub.url}/health -> ${error?.message || String(error)}`;
        }
      }
      this.debugText = [
        `Zeit: ${new Date().toLocaleString()}`,
        `Platform: ${Capacitor.getPlatform()} native=${this.pythonRuntime.isNative}`,
        `Hub URL: ${hub?.url || '-'}`,
        `Python verfügbar: ${runtime.pythonAvailable}`,
        `Hub running: ${runtime.hubRunning}`,
        `Worker running: ${runtime.workerRunning}`,
        `Last error: ${runtime.lastError || '-'}`,
        `Endpoint: ${endpointSummary}`,
      ].join('\n');
    } finally {
      this.debugBusy = false;
    }
  }

  async startEmbeddedNow(): Promise<void> {
    if (!this.isAndroidNative) return;
    this.debugBusy = true;
    try {
      await this.pythonRuntime.ensureEmbeddedControlPlane();
    } catch {
      // status output below will expose the runtime error
    } finally {
      this.debugBusy = false;
      await this.refreshDebugStatus();
    }
  }

  async runPluginHealthCheck(): Promise<void> {
    if (!this.isAndroidNative) return;
    this.debugBusy = true;
    try {
      const res = await this.pythonRuntime.runHealthCheck();
      this.debugText = `${this.debugText}\nPlugin health: ${JSON.stringify(res)}`.trim();
    } catch (error: any) {
      this.debugText = `${this.debugText}\nPlugin health Fehler: ${error?.message || String(error)}`.trim();
    } finally {
      this.debugBusy = false;
    }
  }

  async debugLoginProbe(): Promise<void> {
    if (!this.isAndroidNative) return;
    this.debugBusy = true;
    try {
      const hub = this.resolveHubForLogin();
      if (!hub?.url) {
        this.debugText = `${this.debugText}\nLogin Probe: kein Hub gesetzt`.trim();
        return;
      }
      const body = { username: this.username, password: this.password };
      const result = await firstValueFrom(this.http.post<any>(`${hub.url}/login`, body).pipe(timeout(5000)));
      this.debugText = `${this.debugText}\nLogin Probe OK: ${JSON.stringify(result).slice(0, 280)}`.trim();
    } catch (error: any) {
      const status = error?.status ?? '-';
      const detail = error?.error ? JSON.stringify(error.error).slice(0, 220) : (error?.message || String(error));
      this.debugText = `${this.debugText}\nLogin Probe FEHLER status=${status}: ${detail}`.trim();
    } finally {
      this.debugBusy = false;
    }
  }
}
