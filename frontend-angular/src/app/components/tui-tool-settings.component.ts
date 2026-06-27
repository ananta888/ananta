import { Component, OnInit, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';

import { TuiToolsService, TuiToolProfile, FiletypeRule } from '../services/tui-tools.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { UserAuthService } from '../services/user-auth.service';

@Component({
  standalone: true,
  selector: 'app-tui-tool-settings',
  imports: [FormsModule],
  template: `
    <section class="tui-settings-panel">
      <h3>TUI Tools &amp; Editor Settings</h3>

      @if (error) {
        <p class="error-msg">{{ error }}</p>
      }

      <div class="field-row">
        <label>Default editor</label>
        <span class="value">{{ defaultEditor || '—' }}</span>
      </div>

      <div class="field-row">
        <label>Environment editor allowed</label>
        <span class="value">{{ allowEnvironmentEditor ? 'yes' : 'no' }}</span>
      </div>

      @if (allowedTools.length) {
        <div class="field-block">
          <label>Allowed tools</label>
          <ul class="tool-list">
            @for (tool of allowedTools; track tool) {
              <li>{{ tool }}</li>
            }
          </ul>
        </div>
      }

      @if (toolProfiles.length) {
        <div class="field-block">
          <label>Tool profiles</label>
          <table class="profile-table">
            <thead>
              <tr><th>ID</th><th>Command</th><th>Working dir</th></tr>
            </thead>
            <tbody>
              @for (p of toolProfiles; track p.id) {
                <tr>
                  <td>{{ p.id }}</td>
                  <td><code>{{ p.command }}{{ p.args?.length ? ' ' + p.args.join(' ') : '' }}</code></td>
                  <td>{{ p.working_directory || '—' }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      @if (filetypeRules.length) {
        <div class="field-block">
          <label>Filetype editor mappings</label>
          <table class="profile-table">
            <thead>
              <tr><th>Pattern</th><th>Editor</th></tr>
            </thead>
            <tbody>
              @for (r of filetypeRules; track r.match) {
                <tr>
                  <td><code>{{ r.match }}</code></td>
                  <td>{{ r.editor }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <button class="refresh-btn" (click)="load()" [disabled]="loading">
        {{ loading ? 'Loading…' : 'Refresh' }}
      </button>
    </section>
  `,
  styles: [`
    .tui-settings-panel { padding: 1rem; }
    .field-row { display: flex; gap: 1rem; margin-bottom: 0.5rem; }
    .field-row label { font-weight: 600; min-width: 200px; }
    .field-block { margin-bottom: 1rem; }
    .field-block label { display: block; font-weight: 600; margin-bottom: 0.25rem; }
    .tool-list { margin: 0; padding-left: 1.2rem; }
    .profile-table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
    .profile-table th, .profile-table td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
    .profile-table th { background: #f5f5f5; }
    .error-msg { color: #c00; }
    .refresh-btn { margin-top: 0.5rem; }
  `],
})
export class TuiToolSettingsComponent implements OnInit {
  private svc = inject(TuiToolsService);
  private dir = inject(AgentDirectoryService);
  private auth = inject(UserAuthService);

  loading = false;
  error = '';

  defaultEditor = '';
  allowEnvironmentEditor = true;
  allowedTools: string[] = [];
  toolProfiles: TuiToolProfile[] = [];
  filetypeRules: FiletypeRule[] = [];

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.error = 'No Hub registered. Configure a Hub agent first.';
      return;
    }
    this.loading = true;
    this.error = '';
    this.svc.listTools(hub.url, this.auth.token ?? undefined).subscribe({
      next: (profiles) => {
        this.toolProfiles = profiles;
        this.loading = false;
      },
      error: (err) => {
        this.error = `Could not load tool profiles: ${err?.message ?? err}`;
        this.loading = false;
      },
    });
  }
}
