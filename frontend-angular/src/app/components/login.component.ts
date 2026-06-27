import { Component, inject, OnInit } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { Capacitor } from '@capacitor/core';
import { firstValueFrom, timeout } from 'rxjs';
import { UserAuthService } from '../services/user-auth.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { PythonRuntimeService } from '../services/python-runtime.service';
import { OidcAuthService } from '../services/oidc-auth.service';
import { IdentityBridge } from '../services/identity/identity-bridge';
import { PUBLIC_KEYCLOAK_BASE_URL } from '../services/public-ananta-endpoints';
import { NetworkProfileService } from '../services/network-profile.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
template: `
    <div class="login-container">
      <div class="card login-card">
        <h2>Ananta Login</h2>
        @if (requestedSphere === 'hub') {
          <p class="muted mfa-hint">Für diese Aktion ist eine Hub-Anmeldung erforderlich.</p>
        } @else if (requestedSphere === 'oidc') {
          <p class="muted mfa-hint">Für Pair/WebRTC ist eine Keycloak-Anmeldung erforderlich.</p>
        }
        @if (showHubDirect) {
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
          </form>
        }
        @if (showHubDirect && mfaRequired) {
          <button
            type="button"
            (click)="mfaRequired = false; error = ''"
            class="button-outline btn-full btn-mt-sm"
            aria-label="Zurück zur Passwort-Eingabe">
            Zurück zum Passwort
          </button>
        }
        @if (showOidc) {
          <div class="oidc-divider"><span>Pair-/WebRTC-Zugang</span></div>
          <button type="button" class="secondary btn-full" (click)="loginWithKeycloak()" [disabled]="loading">
            Bei Keycloak anmelden ({{ keycloakHostLabel }})
          </button>
          <p class="muted mfa-hint">
            Diese Anmeldung gilt für Pair Dev und webrtc.ananta.de. Der Hub-Zugang bleibt davon getrennt.
          </p>
          @if (showLinkOption) {
            <button type="button" class="secondary btn-full btn-mt-sm" (click)="linkIdentities()" [disabled]="loading">
              {{ hasOidcIdentity ? 'Hub- und Keycloak-Konto verknüpfen' : 'Bei Keycloak anmelden und Konten verknüpfen' }}
            </button>
          }
          <button type="button" class="secondary btn-full btn-mt-sm" (click)="toggleDeviceFlow()" [disabled]="loading">
            {{ deviceFlowOpen ? 'Device Flow schliessen' : 'Device Flow (TUI-Code)' }}
          </button>
          @if (deviceFlowOpen) {
            <div class="device-flow-panel">
              @if (!deviceFlowData) {
                <button type="button" class="primary btn-full" (click)="startDeviceFlow()" [disabled]="deviceFlowBusy">
                  {{ deviceFlowBusy ? 'Starte...' : 'Device Flow starten' }}
                </button>
              } @else {
                <div class="device-code-box">
                  <div class="device-code-label">Code in Browser oder TUI eingeben:</div>
                  <div class="device-code">{{ deviceFlowData.user_code }}</div>
                  <div class="device-code-url">{{ deviceFlowData.verification_uri }}</div>
                </div>
                @if (deviceFlowError) { <div class="error-msg">{{ deviceFlowError }}</div> }
                @if (deviceFlowBusy) { <div class="hint-text">Warte auf Bestätigung...</div> }
              }
            </div>
          }
        }
      </div>
    </div>
    `
})
export class LoginComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private auth = inject(UserAuthService);
  private dir = inject(AgentDirectoryService);
  private pythonRuntime = inject(PythonRuntimeService);
  private oidc = inject(OidcAuthService);
  private bridge = inject(IdentityBridge);
  private profiles = inject(NetworkProfileService);
  requestedSphere: 'hub' | 'oidc' | null = null;

  deviceFlowOpen = false;
  deviceFlowBusy = false;
  deviceFlowData: { user_code: string; verification_uri: string; device_code: string; interval: number } | null = null;
  deviceFlowError = '';
  private deviceFlowPollHandle: ReturnType<typeof setInterval> | null = null;

  /**
   * Login-mode flag exposed to the template.
   * Public-ananta profile → only OIDC button + device flow.
   * Local/enterprise     → only username/password.
   * If both bridge and hub-direct would be applicable, both are shown.
   */
  get showHubDirect(): boolean {
    return this.bridge.showHubDirectLogin;
  }
  get showOidc(): boolean {
    return this.bridge.showOidcLogin;
  }
  get showLinkOption(): boolean {
    return this.bridge.hubLinkEnabled && !!this.auth.token;
  }
  get hasOidcIdentity(): boolean {
    return !!this.auth.oidcAccessTokenValue;
  }

  ngOnInit(): void {
    const sphere = this.route.snapshot.queryParamMap.get('sphere');
    this.requestedSphere = sphere === 'hub' || sphere === 'oidc' ? sphere : null;
  }

  loginWithKeycloak(): void {
    void this.oidc.startLogin('/');
  }

  async linkIdentities(): Promise<void> {
    const hub = this.resolveHubForLogin();
    const oidcToken = this.auth.oidcAccessTokenValue;
    if (!hub) return;
    if (!oidcToken) {
      await this.oidc.startLogin('/login', true);
      return;
    }
    this.loading = true;
    this.error = '';
    try {
      await firstValueFrom(this.http.post(
        `${hub.url}/auth/oidc/link`,
        { oidc_access_token: oidcToken },
      ));
      this.forgotInfo = 'Hub- und Keycloak-Konto wurden verknüpft.';
    } catch (err: any) {
      this.error = err?.error?.message || 'Konten konnten nicht verknüpft werden.';
    } finally {
      this.loading = false;
    }
  }

  toggleDeviceFlow(): void {
    this.deviceFlowOpen = !this.deviceFlowOpen;
    if (!this.deviceFlowOpen) this.stopDeviceFlow();
  }

  async startDeviceFlow(): Promise<void> {
    this.deviceFlowBusy = true;
    this.deviceFlowError = '';
    try {
      const data = await this.oidc.startDeviceFlow();
      this.deviceFlowData = data;
      this.deviceFlowBusy = false;
      this.deviceFlowPollHandle = setInterval(async () => {
        try {
          const ok = await this.oidc.pollDeviceToken(data.device_code, data.interval);
          if (ok) { this.stopDeviceFlow(); this.router.navigate(['/']); }
        } catch (e: any) {
          this.stopDeviceFlow();
          this.deviceFlowError = String(e?.message ?? 'Fehler');
        }
      }, (data.interval + 1) * 1000);
    } catch (e: any) {
      this.deviceFlowError = String(e?.message ?? 'Device Flow fehlgeschlagen');
      this.deviceFlowBusy = false;
    }
  }

  private stopDeviceFlow(): void {
    if (this.deviceFlowPollHandle) { clearInterval(this.deviceFlowPollHandle); this.deviceFlowPollHandle = null; }
    this.deviceFlowBusy = false;
  }

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
  readonly keycloakHostLabel = PUBLIC_KEYCLOAK_BASE_URL.replace(/^https?:\/\//, '');

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

      await this.auth.setTokens(accessToken, payload?.refresh_token ?? null);
      await this.profiles.load();
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
      const worker = this.dir.get('worker') ?? this.dir.list().find((a) => a.role === 'worker');
      const runtime = await this.pythonRuntime.getRuntimeStatus();
      let endpointSummary = 'Hub endpoint nicht gesetzt';
      let workerEndpointSummary = 'Worker endpoint nicht gesetzt';
      if (hub?.url) {
        try {
          const health = await firstValueFrom(this.http.get<any>(`${hub.url}/health`).pipe(timeout(3000)));
          endpointSummary = `OK ${hub.url}/health -> ${JSON.stringify(health).slice(0, 180)}`;
        } catch (error: any) {
          endpointSummary = `FEHLER ${hub.url}/health -> ${error?.message || String(error)}`;
        }
      }
      if (worker?.url) {
        try {
          const health = await firstValueFrom(this.http.get<any>(`${worker.url}/health`).pipe(timeout(3000)));
          workerEndpointSummary = `OK ${worker.url}/health -> ${JSON.stringify(health).slice(0, 180)}`;
        } catch (error: any) {
          workerEndpointSummary = `FEHLER ${worker.url}/health -> ${error?.message || String(error)}`;
        }
      }
      this.debugText = [
        `Zeit: ${new Date().toLocaleString()}`,
        `Platform: ${Capacitor.getPlatform()} native=${this.pythonRuntime.isNative}`,
        `Hub URL: ${hub?.url || '-'}`,
        `Worker URL: ${worker?.url || '-'}`,
        `Python verfügbar: ${runtime.pythonAvailable}`,
        `Embedded Hub running: ${runtime.hubRunning}`,
        `Embedded Worker running: ${runtime.workerRunning}`,
        `Last error: ${runtime.lastError || '-'}`,
        `Hub Endpoint: ${endpointSummary}`,
        `Worker Endpoint: ${workerEndpointSummary}`,
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
