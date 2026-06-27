import { Component, OnInit, inject } from '@angular/core';


import { AdminFacade } from './admin.facade';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { NotificationService } from '../../services/notification.service';
import { UserAuthService } from '../../services/user-auth.service';

interface DiagnosticEntry {
  label: string;
  value: string;
  status: 'ok' | 'warn' | 'error' | 'info';
}

@Component({
  standalone: true,
  selector: 'app-admin-diagnostics',
  imports: [],
  template: `
    <div class="card">
      <div class="row flex-between">
        <h3>Admin-Diagnose</h3>
        <button class="button-outline" (click)="load()">🔄 Aktualisieren</button>
      </div>
      <p class="muted">Systemzustand und Policy-Übersicht für Administratoren.</p>

      @if (loading) {
        <p class="muted">Lade...</p>
      } @else {
        <table>
          <thead>
            <tr>
              <th>Bereich</th>
              <th>Wert</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            @for (entry of entries; track entry.label) {
              <tr>
                <td>{{ entry.label }}</td>
                <td><code>{{ entry.value }}</code></td>
                <td>
                  <span [class]="badgeClass(entry.status)">{{ entry.status }}</span>
                </td>
              </tr>
            }
          </tbody>
        </table>

        @if (sandboxProfile) {
          <div style="margin-top: 20px;">
            <h4>Aktives Sandbox-Profil</h4>
            <pre style="font-size: 12px; overflow: auto; max-height: 300px;">{{ sandboxProfile }}</pre>
          </div>
        }
      }
    </div>
  `,
  styles: [`
    .badge-ok { color: #28a745; font-weight: 600; }
    .badge-warn { color: #ffc107; font-weight: 600; }
    .badge-error { color: #dc3545; font-weight: 600; }
    .badge-info { color: #17a2b8; font-weight: 600; }
  `],
})
export class AdminDiagnosticsComponent implements OnInit {
  private facade = inject(AdminFacade);
  private dir = inject(AgentDirectoryService);
  private auth = inject(UserAuthService);
  private ns = inject(NotificationService);

  loading = true;
  entries: DiagnosticEntry[] = [];
  sandboxProfile: string | null = null;

  ngOnInit() { this.load(); }

  load() {
    this.loading = true;
    this.entries = [];
    this.sandboxProfile = null;

    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.ns.error('Kein Hub gefunden');
      this.loading = false;
      return;
    }

    const token = this.auth.token ?? undefined;
    this.facade.getConfig(hub.url, token).subscribe({
      next: (cfg: any) => {
        this.entries = this.buildEntries(cfg);
        const sandbox = cfg?.sandbox_profile ?? cfg?.worker_runtime?.sandbox_profile;
        if (sandbox) {
          this.sandboxProfile = JSON.stringify(sandbox, null, 2);
        }
        this.loading = false;
      },
      error: () => {
        this.ns.error('Konfiguration konnte nicht geladen werden');
        this.loading = false;
      },
    });
  }

  private buildEntries(cfg: any): DiagnosticEntry[] {
    const entries: DiagnosticEntry[] = [];

    const provider = cfg?.default_provider ?? cfg?.config?.default_provider ?? '—';
    entries.push({ label: 'LLM Provider', value: String(provider), status: provider !== '—' ? 'ok' : 'warn' });

    const authProvider = cfg?.auth_provider ?? cfg?.config?.auth_provider ?? 'local';
    entries.push({ label: 'Auth Provider', value: String(authProvider), status: 'info' });

    const mfaRequired = cfg?.mfa_required ?? cfg?.config?.mfa_required;
    entries.push({
      label: 'MFA erzwungen',
      value: mfaRequired ? 'ja' : 'nein',
      status: mfaRequired ? 'ok' : 'warn',
    });

    const sandboxClass = cfg?.worker_runtime?.default_isolation_class
      ?? cfg?.sandbox_profile?.command_wrappers?.default_isolation_class
      ?? '—';
    entries.push({
      label: 'Sandbox Isolation Class',
      value: String(sandboxClass),
      status: sandboxClass === 'hardened-high-risk' ? 'ok' : sandboxClass === 'bounded-mutable' ? 'warn' : 'info',
    });

    const proposePolicy = cfg?.propose_policy ?? cfg?.config?.propose_policy;
    const gateEnabled = proposePolicy?.require_approval ?? false;
    entries.push({
      label: 'Mutation Gate (Approval)',
      value: gateEnabled ? 'aktiv' : 'inaktiv',
      status: gateEnabled ? 'ok' : 'warn',
    });

    return entries;
  }

  badgeClass(status: string): string {
    return `badge-${status}`;
  }
}
