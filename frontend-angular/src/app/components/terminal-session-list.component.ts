import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TerminalSession, TerminalApiService } from '../services/terminal-api.service';

@Component({
  standalone: true,
  selector: 'app-terminal-session-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  styles: [`
    .session-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .session-table th {
      text-align: left;
      padding: 6px 10px;
      border-bottom: 1px solid var(--border);
      font-weight: 600;
      color: var(--text-secondary);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .session-table td {
      padding: 6px 10px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }
    .status-badge {
      display: inline-block;
      padding: 1px 7px;
      border-radius: 10px;
      font-size: 11px;
      font-weight: 600;
    }
    .status-running { background: rgba(39,174,96,0.15); color: #27ae60; }
    .status-attached { background: rgba(41,128,185,0.15); color: #2980b9; }
    .status-detached { background: rgba(127,140,141,0.15); color: #7f8c8d; }
    .status-expired, .status-killed, .status-failed {
      background: rgba(192,57,43,0.12); color: #c0392b;
    }
    .risk-high { color: #c0392b; font-weight: 600; font-size: 11px; }
    .type-badge {
      font-size: 11px;
      padding: 1px 6px;
      border-radius: 4px;
      font-family: monospace;
    }
    .type-worker { background: rgba(39,174,96,0.1); color: #1e8449; }
    .type-hub, .type-hub_as_worker { background: rgba(192,57,43,0.1); color: #922b21; }
    .readonly-tag { font-size: 10px; color: var(--text-secondary); margin-left: 4px; }
    .empty-state { padding: 24px; text-align: center; color: var(--text-secondary); font-size: 13px; }
    button.action-btn { font-size: 12px; padding: 3px 9px; margin-right: 4px; }
    button.action-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  `],
  template: `
    <div *ngIf="!sessions || sessions.length === 0" class="empty-state">
      No active terminal sessions.
    </div>

    <table *ngIf="sessions && sessions.length > 0" class="session-table">
      <thead>
        <tr>
          <th>Type</th>
          <th>Target</th>
          <th>Status</th>
          <th>User</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr *ngFor="let s of sessions">
          <td>
            <span class="type-badge" [class]="'type-' + s.target_type">{{ s.target_type }}</span>
            <span *ngIf="isHighRisk(s)" class="risk-high" title="Hub access is high risk">⚠</span>
          </td>
          <td>
            <span style="font-family: monospace; font-size: 12px;">{{ s.target_display_name || s.target_id }}</span>
            <span *ngIf="s.read_only" class="readonly-tag">[read-only]</span>
          </td>
          <td>
            <span class="status-badge" [class]="'status-' + s.status">{{ s.status }}</span>
          </td>
          <td style="font-size: 12px; color: var(--text-secondary);">{{ s.created_by_username || '—' }}</td>
          <td>
            <button
              class="action-btn"
              [disabled]="!canAttach(s) || attaching === s.id"
              (click)="onAttach(s)"
              title="{{ !canAttach(s) ? 'Cannot attach to this session' : 'Open in browser terminal' }}">
              {{ attaching === s.id ? 'Attaching…' : 'Attach' }}
            </button>
            <button
              class="action-btn"
              [disabled]="!canKill(s) || killing === s.id"
              (click)="onKill(s)"
              title="{{ !canKill(s) ? 'Cannot kill this session' : 'Kill session' }}"
              style="color: #c0392b;">
              {{ killing === s.id ? 'Killing…' : 'Kill' }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  `,
})
export class TerminalSessionListComponent {
  @Input() sessions: TerminalSession[] = [];
  @Input() baseUrl = '';
  @Input() token?: string;

  @Output() attachRequested = new EventEmitter<{ sessionId: string; attachToken: string }>();
  @Output() sessionKilled = new EventEmitter<string>();
  @Output() error = new EventEmitter<string>();

  private api = inject(TerminalApiService);
  private cdr = inject(ChangeDetectorRef);

  attaching: string | null = null;
  killing: string | null = null;

  isHighRisk(s: TerminalSession): boolean {
    return s.target_type === 'hub' || s.target_type === 'hub_as_worker';
  }

  canAttach(s: TerminalSession): boolean {
    return s.status === 'running' || s.status === 'detached';
  }

  canKill(s: TerminalSession): boolean {
    return s.status === 'running' || s.status === 'attached' || s.status === 'detached';
  }

  onAttach(s: TerminalSession): void {
    this.attaching = s.id;
    this.api.getAttachToken(this.baseUrl, s.id, this.token).subscribe({
      next: (resp: any) => {
        const attachToken = resp?.data?.attach_token;
        if (!attachToken) {
          this.error.emit('attach_token_failed');
        } else {
          this.attachRequested.emit({ sessionId: s.id, attachToken });
        }
        this.attaching = null;
        this.cdr.markForCheck();
      },
      error: () => {
        this.error.emit('attach_token_error');
        this.attaching = null;
        this.cdr.markForCheck();
      },
    });
  }

  onKill(s: TerminalSession): void {
    if (!confirm(`Kill terminal session ${s.id.slice(0, 8)}…?`)) return;
    this.killing = s.id;
    this.api.killSession(this.baseUrl, s.id, this.token).subscribe({
      next: () => {
        this.sessionKilled.emit(s.id);
        this.killing = null;
        this.cdr.markForCheck();
      },
      error: () => {
        this.error.emit('kill_session_error');
        this.killing = null;
        this.cdr.markForCheck();
      },
    });
  }
}
