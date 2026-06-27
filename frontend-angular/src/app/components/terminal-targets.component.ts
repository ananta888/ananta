import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
} from '@angular/core';

import { TerminalTarget, TerminalApiService } from '../services/terminal-api.service';

@Component({
  standalone: true,
  selector: 'app-terminal-targets',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  styles: [`
    .target-list { display: flex; flex-direction: column; gap: 8px; }
    .target-card {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .target-card.high-risk { border-color: #c0392b; background: rgba(192,57,43,0.06); }
    .target-card.worker { border-color: var(--border); }
    .risk-badge {
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      background: #c0392b;
      color: #fff;
      white-space: nowrap;
    }
    .target-type { font-weight: 600; font-size: 13px; }
    .target-id { font-size: 12px; color: var(--text-secondary, #888); font-family: monospace; }
    .target-actions { display: flex; gap: 6px; }
    .target-actions button { font-size: 12px; padding: 4px 10px; }
    .hub-warning {
      background: rgba(192,57,43,0.1);
      border: 1px solid #c0392b;
      border-radius: 6px;
      padding: 10px 14px;
      margin-bottom: 12px;
      font-size: 13px;
      color: #c0392b;
    }
    .confirmation-dialog {
      background: var(--card-bg);
      border: 1px solid #c0392b;
      border-radius: 8px;
      padding: 16px;
      margin-top: 8px;
    }
  `],
  template: `
    <div class="target-list">
      @if (highRiskTargets.length > 0) {
        <div class="hub-warning">
          ⚠ Hub and Hub-as-Worker terminal access is HIGH RISK — requires explicit permission and provides direct control over the orchestration plane.
        </div>
      }
    
      @if (!targets || targets.length === 0) {
        <div style="color: var(--text-secondary); font-size: 13px; padding: 8px 0;">
          No terminal-capable targets available. Enable TERMINAL_FEATURE_ENABLED on the Hub.
        </div>
      }
    
      @for (t of targets; track t) {
        <div class="target-card" [class.high-risk]="isHighRisk(t)" [class.worker]="!isHighRisk(t)">
          <div style="flex: 1; min-width: 0;">
            <div class="target-type">
              {{ t.target_type }}
              @if (isHighRisk(t)) {
                <span class="risk-badge">HIGH RISK</span>
              }
            </div>
            <div class="target-id">{{ t.target_display_name || t.target_id }}</div>
            @if (t.health) {
              <div style="font-size: 11px; color: var(--text-secondary);">health: {{ t.health }}</div>
            }
          </div>
          <div class="target-actions">
            @if (confirmingTarget !== t.target_id) {
              <button
                [disabled]="!t.capabilities?.create || creating === t.target_id"
                (click)="onCreateClick(t)"
                title="{{ !t.capabilities?.create ? 'No permission to create session' : 'Create terminal session' }}">
                {{ creating === t.target_id ? 'Creating…' : 'Open Terminal' }}
              </button>
            } @else {
              <div class="confirmation-dialog">
                <p><strong>⚠ HIGH RISK:</strong> Opening a {{ confirmingTarget === 'hub' ? 'Hub' : 'Hub-as-Worker' }} terminal gives direct access to the orchestration runtime.</p>
                <p>Continue only if explicitly authorized.</p>
                <div style="display: flex; gap: 8px; margin-top: 8px;">
                  <button (click)="confirmCreate(t)">Confirm — Open Terminal</button>
                  <button (click)="confirmingTarget = null">Cancel</button>
                </div>
              </div>
            }
          </div>
        </div>
      }
    </div>
    `,
})
export class TerminalTargetsComponent {
  @Input() targets: TerminalTarget[] = [];
  @Input() baseUrl = '';
  @Input() token?: string;

  @Output() sessionCreated = new EventEmitter<{ sessionId: string; attachToken: string }>();
  @Output() errorOccurred = new EventEmitter<string>();

  private api = inject(TerminalApiService);
  private cdr = inject(ChangeDetectorRef);

  creating: string | null = null;
  confirmingTarget: string | null = null;

  get highRiskTargets(): TerminalTarget[] {
    return (this.targets || []).filter(t => this.isHighRisk(t));
  }

  isHighRisk(t: TerminalTarget): boolean {
    return t.target_type === 'hub' || t.target_type === 'hub_as_worker';
  }

  onCreateClick(t: TerminalTarget): void {
    if (!t.capabilities?.create) return;
    if (this.isHighRisk(t)) {
      this.confirmingTarget = t.target_id;
      return;
    }
    this.doCreate(t);
  }

  confirmCreate(t: TerminalTarget): void {
    this.confirmingTarget = null;
    this.doCreate(t);
  }

  private doCreate(t: TerminalTarget): void {
    this.creating = t.target_id;
    this.api.createSession(this.baseUrl, {
      target_type: t.target_type,
      target_id: t.target_id,
    }, this.token).subscribe({
      next: (resp: any) => {
        const sessionId = resp?.data?.session?.id;
        if (!sessionId) {
          this.errorOccurred.emit(resp?.data?.reason_code || 'session_create_failed');
          this.creating = null;
          this.cdr.markForCheck();
          return;
        }
        this.api.getAttachToken(this.baseUrl, sessionId, this.token).subscribe({
          next: (tokenResp: any) => {
            const attachToken = tokenResp?.data?.attach_token;
            if (!attachToken) {
              this.errorOccurred.emit('attach_token_failed');
            } else {
              this.sessionCreated.emit({ sessionId, attachToken });
            }
            this.creating = null;
            this.cdr.markForCheck();
          },
          error: (err: any) => {
            this.errorOccurred.emit('attach_token_error');
            this.creating = null;
            this.cdr.markForCheck();
          },
        });
      },
      error: (err: any) => {
        this.errorOccurred.emit('session_create_error');
        this.creating = null;
        this.cdr.markForCheck();
      },
    });
  }
}
