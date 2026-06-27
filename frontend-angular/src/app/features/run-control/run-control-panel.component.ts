import {
  Component, Input, OnInit, OnDestroy, inject,
  signal, computed
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Subscription, interval } from 'rxjs';
import { switchMap, startWith } from 'rxjs/operators';

import { NotificationService } from '../../services/notification.service';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { UserAuthService } from '../../services/user-auth.service';
import { TaskManagementFacade } from '../tasks/task-management.facade';
import {
  RunControlState,
  ApprovalGateSummary,
  BranchCandidate,
  OperatorInstructionSummary,
  RunCommand,
} from '../../services/hub-run-control-api.service';

@Component({
  standalone: true,
  selector: 'app-run-control-panel',
  imports: [FormsModule],
  template: `
<div class="rc-panel">

  <!-- Run-Status Banner -->
  @if (controlState()) {
    <div class="rc-status-row">
      <span class="rc-status-badge" [class]="'rc-status-' + (controlState()!.run_status ?? 'unknown')">
        {{ runStatusLabel(controlState()!.run_status) }}
      </span>
      <span class="rc-task-status muted">Task: {{ controlState()!.task_status ?? '–' }}</span>
      <span class="rc-computed-at muted">Stand: {{ age(controlState()!.computed_at) }}</span>
    </div>
  }

  <!-- Action Buttons -->
  <div class="rc-actions row gap-sm mt-sm">
    <button
      class="btn btn-sm"
      [disabled]="busy() || !canPause()"
      (click)="doPause()"
      aria-label="Task pausieren"
      [title]="canPause() ? 'Task pausieren' : 'Pausieren nicht verfügbar (Status: ' + (controlState()?.task_status ?? '?') + ')'">
      ⏸ Pause
    </button>
    <button
      class="btn btn-sm btn-success"
      [disabled]="busy() || !canResume()"
      (click)="doResume()"
      aria-label="Task fortsetzen"
      [title]="canResume() ? 'Task fortsetzen' : 'Fortsetzen nicht verfügbar'">
      ▶ Resume
    </button>
    <button
      class="btn btn-sm btn-danger"
      [disabled]="busy() || !canCancel()"
      (click)="doCancel()"
      aria-label="Task abbrechen"
      [title]="canCancel() ? 'Task abbrechen' : 'Abbrechen nicht verfügbar'">
      ✕ Cancel
    </button>
    <button
      class="btn btn-sm"
      [disabled]="busy() || !canRetry()"
      (click)="doRetry()"
      aria-label="Task wiederholen"
      [title]="canRetry() ? 'Task wiederholen' : 'Retry nicht verfügbar'">
      ↺ Retry
    </button>
    <button class="btn btn-sm btn-ghost" (click)="refresh()" [disabled]="loading()" title="Status aktualisieren">
      ⟳ Refresh
    </button>
  </div>

  <!-- Instruction Injection (RC-030) -->
  <details class="rc-section mt-md" [open]="showInjectForm()">
    <summary class="rc-section-title" (click)="toggleInjectForm()">
      Anweisung injizieren
      @if (controlState()?.active_instruction) {
        <span class="badge badge-yellow ml-sm">aktiv</span>
      }
    </summary>
    <div class="rc-inject-form mt-sm">
      @if (controlState()?.active_instruction; as instr) {
        <div class="rc-active-instr mb-sm">
          <span class="badge badge-yellow">{{ instr.instruction_class }}</span>
          <span class="ml-sm muted">{{ instr.mode }}</span>
          <p class="rc-instr-text mt-xs">{{ instr.text }}</p>
          <small class="muted">von {{ instr.actor }} – {{ age(instr.created_at) }}</small>
        </div>
      }
      <label class="block">
        Anweisung
        <textarea
          class="rc-inject-textarea"
          rows="3"
          [(ngModel)]="injectionText"
          placeholder="z. B. 'Keine React-Lösung, Angular weiterverwenden.'"
          maxlength="4000">
        </textarea>
        <small class="muted">{{ injectionText.length }}/4000 Zeichen</small>
      </label>
      <div class="row gap-sm mt-xs">
        <label class="inline-label">
          Modus
          <select [(ngModel)]="injectionMode" class="select-sm">
            <option value="next_iteration_instruction">Bei nächster Iteration</option>
            <option value="pause_then_apply">Pausieren, dann anwenden</option>
            <option value="context_note_only">Nur als Kontext-Notiz</option>
          </select>
        </label>
        <label class="inline-label">
          Klasse
          <select [(ngModel)]="injectionClass" class="select-sm">
            <option value="constraint">Constraint</option>
            <option value="correction">Korrektur</option>
            <option value="preference">Präferenz</option>
            <option value="branch_hint">Branch-Hinweis</option>
            <option value="stop_condition">Stop-Bedingung</option>
          </select>
        </label>
      </div>
      <button
        class="btn btn-sm btn-primary mt-sm"
        [disabled]="busy() || !injectionText.trim()"
        (click)="doInject()">
        Anweisung senden
      </button>
    </div>
  </details>

  <!-- Pending Approvals (RC-040) -->
  @if ((controlState()?.pending_approvals ?? []).length > 0) {
    <div class="rc-section mt-md">
      <div class="rc-section-title">
        Approval Gates
        <span class="badge badge-warning ml-sm">{{ controlState()!.pending_approvals.length }}</span>
      </div>
      @for (gate of controlState()!.pending_approvals; track gate.request_id) {
        <div class="rc-gate-card mt-sm">
          <div class="row gap-sm align-center">
            <span class="badge" [class]="'rc-risk-' + gate.risk_class">{{ gate.risk_class }}</span>
            <strong>{{ gate.tool_name }}</strong>
            @if (gate.k_class) { <span class="muted">{{ gate.k_class }}</span> }
          </div>
          <div class="rc-gate-meta mt-xs">
            <span class="muted">Digest: {{ gate.digest_prefix }}…</span>
            @if (gate.expires_at) {
              <span class="muted ml-sm">Läuft ab: {{ formatTs(gate.expires_at) }}</span>
            }
            @if (gate.has_content_payload) {
              <span class="badge badge-info ml-sm">mit Inhalt</span>
            }
          </div>
          @if (gate.scope_summary && objectKeys(gate.scope_summary).length) {
            <div class="rc-scope-summary mt-xs">
              @for (entry of objectEntries(gate.scope_summary); track entry[0]) {
                <span class="rc-scope-tag">{{ entry[0] }}: {{ entry[1] }}</span>
              }
            </div>
          }
          <div class="row gap-sm mt-sm">
            <button
              class="btn btn-sm btn-success"
              [disabled]="busy()"
              (click)="doApproveGate(gate.request_id)"
              aria-label="Gate genehmigen">
              ✓ Genehmigen
            </button>
            <div class="row gap-xs align-center">
              <input
                type="text"
                class="input-sm"
                [(ngModel)]="denyReasons[gate.request_id]"
                placeholder="Ablehnungsgrund"
                style="width:160px">
              <button
                class="btn btn-sm btn-danger"
                [disabled]="busy()"
                (click)="doDenyGate(gate.request_id)"
                aria-label="Gate ablehnen">
                ✕ Ablehnen
              </button>
            </div>
          </div>
        </div>
      }
    </div>
  }

  <!-- Branch / Variant Selection (RC-050) -->
  @if ((controlState()?.branches ?? []).length > 0) {
    <div class="rc-section mt-md">
      <div class="rc-section-title">
        Branch-Auswahl
        <span class="badge badge-info ml-sm">{{ controlState()!.branches.length }}</span>
      </div>
      <div class="rc-branch-grid mt-sm">
        @for (branch of controlState()!.branches; track branch.branch_id) {
          <div class="rc-branch-card" [class.rc-branch-selected]="branch.status === 'selected'">
            <div class="row gap-sm align-center">
              <span class="badge" [class]="'rc-branch-' + branch.status">{{ branch.status }}</span>
              <strong>{{ branch.label }}</strong>
              <span class="muted">{{ branch.branch_type }}</span>
            </div>
            @if (branch.description) {
              <p class="rc-branch-desc mt-xs">{{ branch.description }}</p>
            }
            @if (branch.status === 'proposed' || branch.status === 'active') {
              <button
                class="btn btn-sm btn-primary mt-sm"
                [disabled]="busy()"
                (click)="doSelectBranch(branch.branch_id)"
                [attr.aria-label]="'Branch ' + branch.label + ' auswählen'">
                Diesen Branch wählen
              </button>
            }
            @if (branch.status === 'selected') {
              <div class="mt-xs">
                <span class="badge badge-success">✓ Ausgewählt</span>
                @if (branch.selected_at) {
                  <small class="muted ml-sm">{{ age(branch.selected_at) }}</small>
                }
              </div>
            }
          </div>
        }
      </div>
    </div>
  }

  <!-- Command Timeline -->
  @if ((controlState()?.last_events ?? []).length > 0) {
    <details class="rc-section mt-md">
      <summary class="rc-section-title">Command-Historie ({{ controlState()!.last_events.length }})</summary>
      <div class="rc-timeline mt-sm">
        @for (evt of controlState()!.last_events; track evt.command_id) {
          <div class="rc-timeline-entry">
            <span class="badge" [class]="'rc-cmd-' + evt.status">{{ evt.status }}</span>
            <span class="ml-sm">{{ evt.type }}</span>
            <span class="muted ml-sm">{{ evt.requested_by }}</span>
            <span class="muted ml-sm">{{ formatTs(evt.requested_at) }}</span>
            @if (evt.result && objectKeys(evt.result).length) {
              <span class="muted ml-sm rc-cmd-result">
                {{ resultSummary(evt.result) }}
              </span>
            }
          </div>
        }
      </div>
    </details>
  }

  @if (errorMessage()) {
    <div class="state-banner error mt-sm">{{ errorMessage() }}</div>
  }
</div>
`,
  styles: [`
.rc-panel { font-size: 0.9rem; }
.rc-status-row { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.rc-status-badge { padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.rc-status-running { background: #1a7f37; color: #fff; }
.rc-status-paused { background: #d29922; color: #fff; }
.rc-status-waiting_for_approval { background: #e36209; color: #fff; }
.rc-status-waiting_for_branch_selection { background: #6e40c9; color: #fff; }
.rc-status-applying_intervention { background: #0969da; color: #fff; }
.rc-status-cancelled, .rc-status-failed { background: #cf222e; color: #fff; }
.rc-status-completed { background: #1a7f37; color: #fff; }
.rc-status-planning { background: #0550ae; color: #fff; }
.rc-status-unknown { background: #6e7781; color: #fff; }
.rc-section { border: 1px solid var(--border-color, #d0d7de); border-radius: 6px; padding: 0.75rem; }
.rc-section-title { font-weight: 600; cursor: pointer; user-select: none; display: flex; align-items: center; }
.rc-gate-card { border: 1px solid var(--border-color, #d0d7de); border-radius: 4px; padding: 0.6rem; }
.rc-risk-critical { background: #cf222e; color: #fff; }
.rc-risk-high { background: #e36209; color: #fff; }
.rc-risk-medium { background: #d29922; color: #fff; }
.rc-risk-low { background: #0969da; color: #fff; }
.rc-risk-unknown, .rc-risk-execution { background: #6e7781; color: #fff; }
.rc-scope-summary { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.rc-scope-tag { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 3px; padding: 1px 6px; font-size: 0.75rem; }
.rc-branch-grid { display: flex; flex-direction: column; gap: 0.5rem; }
.rc-branch-card { border: 1px solid var(--border-color, #d0d7de); border-radius: 4px; padding: 0.6rem; }
.rc-branch-selected { border-color: #1a7f37; background: #d1fae5; }
.rc-branch-proposed { background: #f6f8fa; color: #24292f; }
.rc-branch-selected { background: #1a7f37; color: #fff; }
.rc-branch-paused { background: #d29922; color: #fff; }
.rc-branch-rejected, .rc-branch-superseded { background: #6e7781; color: #fff; }
.rc-branch-desc { margin: 0; font-size: 0.82rem; color: var(--text-muted, #57606a); }
.rc-timeline { display: flex; flex-direction: column; gap: 0.25rem; max-height: 240px; overflow-y: auto; }
.rc-timeline-entry { display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem; font-size: 0.8rem; padding: 2px 0; border-bottom: 1px solid var(--border-color-light, #f0f0f0); }
.rc-cmd-applied { background: #1a7f37; color: #fff; }
.rc-cmd-rejected_by_policy { background: #e36209; color: #fff; }
.rc-cmd-failed { background: #cf222e; color: #fff; }
.rc-cmd-accepted, .rc-cmd-pending_safe_point { background: #0969da; color: #fff; }
.rc-cmd-result { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rc-inject-textarea { width: 100%; font-size: 0.85rem; resize: vertical; }
.rc-active-instr { background: #fffbcc; border: 1px solid #d29922; border-radius: 4px; padding: 0.5rem; }
.rc-instr-text { margin: 0.25rem 0 0; font-style: italic; }
.rc-task-status { font-size: 0.78rem; }
.rc-computed-at { font-size: 0.72rem; }
.rc-gate-meta { font-size: 0.78rem; }
.select-sm { font-size: 0.82rem; padding: 2px 4px; }
.input-sm { font-size: 0.82rem; padding: 2px 6px; border: 1px solid #d0d7de; border-radius: 4px; }
.inline-label { display: flex; flex-direction: column; gap: 2px; font-size: 0.82rem; }
`],
})
export class RunControlPanelComponent implements OnInit, OnDestroy {
  @Input({ required: true }) taskId!: string;
  @Input() goalId?: string;
  @Input() hubUrl?: string;

  private taskFacade = inject(TaskManagementFacade);
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);

  controlState = signal<RunControlState | null>(null);
  loading = signal(false);
  busy = signal(false);
  errorMessage = signal<string | null>(null);

  injectionText = '';
  injectionMode = 'next_iteration_instruction';
  injectionClass = 'constraint';
  denyReasons: Record<string, string> = {};
  _showInjectForm = false;
  showInjectForm = signal(false);

  private sub?: Subscription;

  private get hub(): string {
    return this.hubUrl ?? (this.dir.list().find(a => a.role === 'hub')?.url ?? '');
  }

  private get token(): string | undefined {
    return this.auth.token ?? undefined;
  }

  ngOnInit(): void {
    this.loadState();
    this.sub = interval(15_000).pipe(startWith(0)).subscribe(() => this.loadState(true));
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  loadState(silent = false): void {
    if (!silent) this.loading.set(true);
    this.taskFacade.getTaskControlState(this.hub, this.taskId, this.goalId, this.token)
      .subscribe({
        next: r => {
          this.controlState.set(r.control_state);
          this.errorMessage.set(null);
          this.loading.set(false);
        },
        error: e => {
          if (!silent) {
            this.errorMessage.set(this.ns.fromApiError(e, 'Control-State konnte nicht geladen werden'));
          }
          this.loading.set(false);
        },
      });
  }

  refresh(): void { this.loadState(); }

  toggleInjectForm(): void {
    this._showInjectForm = !this._showInjectForm;
    this.showInjectForm.set(this._showInjectForm);
  }

  // ── Status helpers ────────────────────────────────────────────────────────────

  canPause = computed(() => {
    const ts = this.controlState()?.task_status;
    return !!ts && ['todo', 'created', 'assigned', 'in_progress', 'delegated', 'proposing', 'waiting_for_review', 'blocked_by_dependency'].includes(ts);
  });

  canResume = computed(() => this.controlState()?.task_status === 'paused');

  canCancel = computed(() => {
    const ts = this.controlState()?.task_status;
    return !!ts && !['cancelled', 'completed', 'failed'].includes(ts);
  });

  canRetry = computed(() => {
    const ts = this.controlState()?.task_status;
    return ts === 'failed' || ts === 'cancelled' || ts === 'verification_failed';
  });

  runStatusLabel(status: string | null | undefined): string {
    const map: Record<string, string> = {
      running: 'Läuft',
      paused: 'Pausiert',
      waiting_for_approval: 'Wartet auf Approval',
      waiting_for_branch_selection: 'Wartet auf Branch-Auswahl',
      applying_intervention: 'Anweisung ausstehend',
      planning: 'Planung',
      cancelling: 'Wird abgebrochen',
      cancelled: 'Abgebrochen',
      completed: 'Abgeschlossen',
      failed: 'Fehlgeschlagen',
    };
    return map[status ?? ''] ?? (status ?? 'Unbekannt');
  }

  age(ts: number): string {
    if (!ts) return '';
    const diff = Math.floor((Date.now() / 1000) - ts);
    if (diff < 60) return `vor ${diff}s`;
    if (diff < 3600) return `vor ${Math.floor(diff / 60)}min`;
    return `vor ${Math.floor(diff / 3600)}h`;
  }

  formatTs(ts: number): string {
    if (!ts) return '–';
    return new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
  }

  objectKeys(obj: Record<string, unknown>): string[] {
    return Object.keys(obj);
  }

  objectEntries(obj: Record<string, unknown>): [string, unknown][] {
    return Object.entries(obj);
  }

  resultSummary(result: Record<string, unknown>): string {
    const err = result['error'];
    if (err) return `Fehler: ${err}`;
    const status = result['status'] ?? result['new_status'];
    if (status) return String(status);
    return JSON.stringify(result).slice(0, 60);
  }

  // ── Mutations ─────────────────────────────────────────────────────────────────

  doPause(): void {
    this.exec('pause', () => this.taskFacade.pauseTask(this.hub, this.taskId, this.token));
  }

  doResume(): void {
    this.exec('resume', () => this.taskFacade.resumeTask(this.hub, this.taskId, undefined, this.token));
  }

  doCancel(): void {
    if (!confirm('Task wirklich abbrechen?')) return;
    this.exec('cancel', () => this.taskFacade.cancelTask(this.hub, this.taskId, this.token));
  }

  doRetry(): void {
    this.exec('retry', () => this.taskFacade.retryTask(this.hub, this.taskId, this.token));
  }

  doInject(): void {
    const text = this.injectionText.trim();
    if (!text) return;
    this.exec('inject', () =>
      this.taskFacade.injectInstruction(this.hub, this.taskId, text, this.injectionMode, this.injectionClass, this.token)
    ).then(() => { this.injectionText = ''; });
  }

  doApproveGate(approvalId: string): void {
    this.exec('approve', () =>
      this.taskFacade.decideApproval(this.hub, this.taskId, approvalId, 'granted', undefined, this.token)
    );
  }

  doDenyGate(approvalId: string): void {
    const reason = this.denyReasons[approvalId]?.trim() || '';
    if (!reason) {
      this.ns.info('Bitte einen Ablehnungsgrund angeben.');
      return;
    }
    this.exec('deny', () =>
      this.taskFacade.decideApproval(this.hub, this.taskId, approvalId, 'denied', reason, this.token)
    ).then(() => { delete this.denyReasons[approvalId]; });
  }

  doSelectBranch(branchId: string): void {
    this.exec('select_branch', () =>
      this.taskFacade.selectBranch(this.hub, this.taskId, branchId, undefined, this.token)
    );
  }

  private exec(label: string, fn: () => any): Promise<void> {
    this.busy.set(true);
    this.errorMessage.set(null);
    return new Promise(resolve => {
      fn().subscribe({
        next: (r: any) => {
          const cmd = r?.command;
          if (cmd?.status === 'rejected_by_policy') {
            this.ns.info(`${label}: ${cmd.result?.error ?? 'rejected_by_policy'}`);
          } else if (cmd?.status === 'failed') {
            this.ns.error(`${label} fehlgeschlagen: ${cmd.result?.error ?? 'unknown'}`);
          } else {
            this.ns.success(`${label} erfolgreich`);
          }
          this.busy.set(false);
          this.loadState(true);
          resolve();
        },
        error: (e: any) => {
          this.errorMessage.set(this.ns.fromApiError(e, `${label} fehlgeschlagen`));
          this.busy.set(false);
          resolve();
        },
      });
    });
  }
}
