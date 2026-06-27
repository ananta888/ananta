import { Component, OnInit, inject } from '@angular/core';
import { Router } from '@angular/router';
import { OidcAuthService } from '../services/oidc-auth.service';

@Component({
  selector: 'app-oidc-callback',
  standalone: true,
  template: `
    <div style="font-family:ui-monospace,monospace;background:#0b1220;color:#c8d8f8;min-height:100vh;display:flex;align-items:center;justify-content:center;font-size:14px;">
      @if (error) {
        <div style="color:#fb7185;">Login fehlgeschlagen: {{ error }}</div>
      } @else if (isPopup) {
        <div style="color:#7fffd4;">Login erfolgreich – Fenster wird geschlossen…</div>
      } @else {
        <div style="color:#7fffd4;">Authentifizierung läuft…</div>
      }
    </div>
  `,
})
export class OidcCallbackComponent implements OnInit {
  private oidc = inject(OidcAuthService);
  private router = inject(Router);
  error = '';
  isPopup = false;

  async ngOnInit(): Promise<void> {
    this.isPopup = !!window.opener;
    try {
      const query = new URLSearchParams(window.location.search);
      // Only `oidc_code` is a one-time Hub broker code. A plain `code` is
      // the standard OIDC authorization code and must go through PKCE.
      const backendCode = query.get('oidc_code');
      if (backendCode) {
        const ok = await this.oidc.handleBackendCallback();
        if (ok && this.isPopup) {
          window.close();
        } else if (!ok) {
          this.error = 'Backend-OIDC-Austausch fehlgeschlagen.';
        }
        return;
      }

      if (this.isPopup) {
        // Popup-PKCE flow: reads from localStorage, closes window on success
        const ok = await this.oidc.handleCallbackForPopup();
        if (ok) {
          window.close();
        } else {
          this.error = 'Ungültige Callback-Parameter.';
          setTimeout(() => window.close(), 3000);
        }
      } else {
        const ok = await this.oidc.handleCallback();
        if (!ok) { this.error = 'Ungültige Callback-Parameter.'; }
      }
    } catch (e: any) {
      this.error = String(e?.message ?? 'Unbekannter Fehler');
      if (this.isPopup) {
        setTimeout(() => window.close(), 3000);
      } else {
        setTimeout(() => this.router.navigate(['/login']), 3000);
      }
    }
  }
}
