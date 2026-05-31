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
      } @else {
        <div style="color:#7fffd4;">Authentifizierung läuft...</div>
      }
    </div>
  `,
})
export class OidcCallbackComponent implements OnInit {
  private oidc = inject(OidcAuthService);
  private router = inject(Router);
  error = '';

  async ngOnInit(): Promise<void> {
    try {
      const ok = await this.oidc.handleCallback();
      if (!ok) { this.error = 'Ungültige Callback-Parameter.'; }
    } catch (e: any) {
      this.error = String(e?.message ?? 'Unbekannter Fehler');
      setTimeout(() => this.router.navigate(['/login']), 3000);
    }
  }
}
