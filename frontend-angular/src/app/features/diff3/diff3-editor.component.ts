import {
  Component, OnInit, OnDestroy, inject, signal, computed, ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import {
  Diff3ApiService, Diff3Session, DiffPanel, AiMode, PanelId, LayoutMode, SourceKind, AiDiffResponse,
} from './diff3-api.service';

type PanelSetup = 'empty' | 'current_diff' | 'output_artifact' | 'ai';

const AI_MODES: AiMode[] = ['review', 'explain', 'risk', 'tests', 'patch', 'chat'];

const AI_MODE_LABELS: Record<AiMode, string> = {
  review: 'Review', explain: 'Explain', risk: 'Risk', tests: 'Tests', patch: 'Patch', chat: 'Chat',
};

const LAYOUT_LABELS: Record<LayoutMode, string> = {
  equal: 'Gleich', 'left-wide': 'Links breit', 'right-wide': 'Rechts breit',
  'focus-a': 'Fokus A', 'focus-b': 'Fokus B', 'focus-c': 'Fokus C',
};

@Component({
  selector: 'app-diff3-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="diff3-root" [attr.data-layout]="session()?.layout_mode ?? 'equal'">

  <!-- ── Toolbar ── -->
  <div class="diff3-toolbar">
    <div class="toolbar-left">
      <span class="diff3-title">Three-Way Flex Diff</span>
      <span class="session-id" *ngIf="session()">{{ session()!.session_id }}</span>
    </div>

    <div class="toolbar-center">
      <!-- Layout selector -->
      <select class="toolbar-select" [ngModel]="session()?.layout_mode" (ngModelChange)="onLayoutChange($event)"
              [disabled]="!session()">
        <option *ngFor="let lm of layoutModes" [value]="lm">{{ layoutLabels[lm] }}</option>
      </select>

      <!-- Sync scroll -->
      <label class="toolbar-toggle">
        <input type="checkbox" [ngModel]="syncScroll()" (ngModelChange)="onSyncToggle($event)"
               [disabled]="!session()">
        Sync
      </label>
    </div>

    <div class="toolbar-right">
      <button class="btn-sm btn-ghost" (click)="newSession()" [disabled]="loading()">Neu</button>
    </div>
  </div>

  <!-- ── Error bar ── -->
  <div class="diff3-error" *ngIf="error()">{{ error() }}</div>

  <!-- ── Loading ── -->
  <div class="diff3-loading" *ngIf="loading() && !session()">Lade Session…</div>

  <!-- ── Three panels ── -->
  <div class="diff3-panels" *ngIf="session()">
    <div class="diff3-panel"
         *ngFor="let pid of panelIds"
         [class.active]="session()!.active_panel === pid"
         [attr.data-panel]="pid"
         (click)="onPanelClick(pid)">

      <!-- Panel header -->
      <div class="panel-header">
        <span class="panel-label">Panel {{ pid }}</span>
        <span class="panel-type">{{ getPanelType(pid) }}</span>
        <div class="panel-controls">
          <select class="panel-select" [ngModel]="getPanelSetup(pid)"
                  (ngModelChange)="onPanelSetupChange(pid, $event)"
                  (click)="$event.stopPropagation()">
            <option value="empty">Leer</option>
            <option value="current_diff">Git Diff</option>
            <option value="output_artifact">Artifact</option>
            <option value="ai">AI</option>
          </select>
        </div>
      </div>

      <!-- Source detail / AI mode selector -->
      <ng-container [ngSwitch]="getPanelSetup(pid)">

        <div *ngSwitchCase="'output_artifact'" class="panel-source-input" (click)="$event.stopPropagation()">
          <input class="source-input" type="text"
                 [placeholder]="'Output-Artifact-ID…'"
                 [ngModel]="artifactInputs[pid]"
                 (ngModelChange)="artifactInputs[pid] = $event"
                 (keydown.enter)="onArtifactEnter(pid)">
          <button class="btn-xs" (click)="onArtifactEnter(pid)">Laden</button>
        </div>

        <div *ngSwitchCase="'current_diff'" class="panel-source-input" (click)="$event.stopPropagation()">
          <input class="source-input" type="text"
                 [placeholder]="'Pfad-Filter (optional)'"
                 [ngModel]="filterInputs[pid]"
                 (ngModelChange)="filterInputs[pid] = $event"
                 (keydown.enter)="onCurrentDiffEnter(pid)">
          <select class="panel-select-sm" [ngModel]="renderModeInputs[pid]"
                  (ngModelChange)="renderModeInputs[pid] = $event">
            <option value="unified">Unified</option>
            <option value="summary">Summary</option>
          </select>
          <button class="btn-xs" (click)="onCurrentDiffEnter(pid)">Setzen</button>
        </div>

        <div *ngSwitchCase="'ai'" class="panel-ai-controls" (click)="$event.stopPropagation()">
          <div class="ai-mode-tabs">
            <button *ngFor="let m of aiModes"
                    class="ai-mode-tab"
                    [class.active]="aiMode() === m"
                    (click)="onAiModeSelect(m)">
              {{ aiModeLabels[m] }}
            </button>
          </div>
          <button class="btn-run" [disabled]="aiRunning()" (click)="onRunAi()">
            {{ aiRunning() ? 'Läuft…' : '▶ Run AI' }}
          </button>
        </div>

      </ng-container>

      <!-- Panel body -->
      <div class="panel-body">
        <ng-container [ngSwitch]="getPanelSetup(pid)">

          <div *ngSwitchCase="'empty'" class="panel-empty">
            <span>Kein Inhalt. Wähle eine Quelle oben.</span>
          </div>

          <div *ngSwitchCase="'current_diff'" class="panel-diff-content">
            <div class="diff-meta">
              <span>Quelle: {{ getPanelSourceLabel(pid) }}</span>
              <span>Render: {{ getPanelRenderMode(pid) }}</span>
            </div>
            <pre class="diff-preview">{{ getPanelDiffPreview(pid) }}</pre>
          </div>

          <div *ngSwitchCase="'output_artifact'" class="panel-diff-content">
            <div class="diff-meta">
              <span>Artifact: {{ getPanelSourceLabel(pid) }}</span>
            </div>
            <pre class="diff-preview">{{ getPanelDiffPreview(pid) }}</pre>
          </div>

          <div *ngSwitchCase="'ai'" class="panel-ai-content">
            <ng-container *ngIf="aiStatus() !== 'idle'">
              <div class="ai-status-bar" [attr.data-status]="aiStatus()">
                {{ aiStatusLabel() }}
              </div>
            </ng-container>
            <ng-container *ngIf="aiResponse()">
              <div class="ai-summary">{{ aiResponse()!.summary }}</div>
              <div class="ai-section" *ngIf="aiResponse()!.findings.length">
                <div class="ai-section-title">Findings</div>
                <ul class="ai-list">
                  <li *ngFor="let f of aiResponse()!.findings">{{ f }}</li>
                </ul>
              </div>
              <div class="ai-section" *ngIf="aiResponse()!.risks.length">
                <div class="ai-section-title">Risks</div>
                <ul class="ai-list ai-list-risk">
                  <li *ngFor="let r of aiResponse()!.risks">{{ r }}</li>
                </ul>
              </div>
              <div class="ai-section" *ngIf="aiResponse()!.suggested_tests.length">
                <div class="ai-section-title">Suggested Tests</div>
                <ul class="ai-list">
                  <li *ngFor="let t of aiResponse()!.suggested_tests">{{ t }}</li>
                </ul>
              </div>
              <div class="ai-section" *ngIf="aiResponse()!.patch_suggestions.length">
                <div class="ai-section-title">Patch</div>
                <pre class="ai-patch" *ngFor="let p of aiResponse()!.patch_suggestions">{{ p }}</pre>
              </div>
            </ng-container>
            <div class="ai-idle-hint" *ngIf="aiStatus() === 'idle' && !aiResponse()">
              Wähle einen Modus und klicke "▶ Run AI"
            </div>
          </div>

        </ng-container>
      </div>

    </div><!-- /panel -->
  </div><!-- /panels -->

</div><!-- /root -->
  `,
  styles: [`
    .diff3-root {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      background: #0d1117;
      color: #e6edf3;
      font-family: ui-monospace, 'JetBrains Mono', monospace;
      font-size: 13px;
    }
    .diff3-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 6px 12px;
      background: #161b22;
      border-bottom: 1px solid #30363d;
      gap: 8px;
      flex-shrink: 0;
    }
    .toolbar-left, .toolbar-center, .toolbar-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .diff3-title { font-weight: 600; color: #58a6ff; }
    .session-id { font-size: 11px; color: #8b949e; }
    .toolbar-select {
      background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
      border-radius: 4px; padding: 2px 6px; font-size: 12px; cursor: pointer;
    }
    .toolbar-toggle { display: flex; align-items: center; gap: 4px; font-size: 12px; cursor: pointer; }
    .btn-sm {
      padding: 3px 10px; border-radius: 4px; border: 1px solid #30363d;
      background: #21262d; color: #e6edf3; cursor: pointer; font-size: 12px;
    }
    .btn-sm:hover:not(:disabled) { background: #30363d; }
    .btn-sm:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn-ghost { background: transparent; }
    .diff3-error {
      padding: 6px 12px; background: #3d1f1f; color: #ff7b72;
      border-bottom: 1px solid #6f1919; font-size: 12px; flex-shrink: 0;
    }
    .diff3-loading { padding: 12px; color: #8b949e; text-align: center; }

    /* Panels */
    .diff3-panels {
      display: flex;
      flex: 1;
      min-height: 0;
      gap: 1px;
      background: #30363d;
    }
    .diff3-panel {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-width: 0;
      background: #0d1117;
      cursor: pointer;
      transition: outline 0.1s;
    }
    .diff3-panel.active { outline: 1px solid #58a6ff; }

    /* Layout modes */
    :host-context([data-layout="left-wide"]) .diff3-panel[data-panel="A"] { flex: 2; }
    :host-context([data-layout="right-wide"]) .diff3-panel[data-panel="C"] { flex: 2; }
    :host-context([data-layout="focus-a"]) .diff3-panel[data-panel="B"],
    :host-context([data-layout="focus-a"]) .diff3-panel[data-panel="C"] { flex: 0 0 0; overflow: hidden; }
    :host-context([data-layout="focus-b"]) .diff3-panel[data-panel="A"],
    :host-context([data-layout="focus-b"]) .diff3-panel[data-panel="C"] { flex: 0 0 0; overflow: hidden; }
    :host-context([data-layout="focus-c"]) .diff3-panel[data-panel="A"],
    :host-context([data-layout="focus-c"]) .diff3-panel[data-panel="B"] { flex: 0 0 0; overflow: hidden; }

    .panel-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 5px 10px;
      background: #161b22;
      border-bottom: 1px solid #30363d;
      flex-shrink: 0;
    }
    .panel-label { font-weight: 700; color: #58a6ff; }
    .panel-type { font-size: 11px; color: #8b949e; flex: 1; }
    .panel-controls { display: flex; gap: 4px; }
    .panel-select {
      background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
      border-radius: 3px; padding: 1px 4px; font-size: 11px; cursor: pointer;
    }
    .panel-select-sm { font-size: 11px; padding: 1px 3px; background: #0d1117;
      color: #e6edf3; border: 1px solid #30363d; border-radius: 3px; }

    .panel-source-input {
      display: flex; align-items: center; gap: 6px;
      padding: 4px 10px; background: #111318; border-bottom: 1px solid #30363d; flex-shrink: 0;
    }
    .source-input {
      flex: 1; background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
      border-radius: 3px; padding: 2px 6px; font-size: 11px; font-family: inherit;
    }
    .btn-xs {
      padding: 2px 8px; border-radius: 3px; border: 1px solid #30363d;
      background: #21262d; color: #e6edf3; cursor: pointer; font-size: 11px;
    }
    .btn-xs:hover { background: #30363d; }

    .panel-ai-controls {
      display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
      padding: 5px 10px; background: #111318; border-bottom: 1px solid #30363d; flex-shrink: 0;
    }
    .ai-mode-tabs { display: flex; gap: 2px; }
    .ai-mode-tab {
      padding: 2px 8px; border-radius: 3px; border: 1px solid #30363d;
      background: #21262d; color: #8b949e; cursor: pointer; font-size: 11px;
    }
    .ai-mode-tab.active { background: #1f3358; color: #58a6ff; border-color: #388bfd; }
    .ai-mode-tab:hover:not(.active) { background: #30363d; }
    .btn-run {
      padding: 3px 12px; border-radius: 4px; border: 1px solid #388bfd;
      background: #1f3358; color: #58a6ff; cursor: pointer; font-size: 12px; font-weight: 600;
      margin-left: auto;
    }
    .btn-run:hover:not(:disabled) { background: #2a4a7f; }
    .btn-run:disabled { opacity: 0.4; cursor: not-allowed; }

    .panel-body { flex: 1; overflow-y: auto; padding: 8px 10px; min-height: 0; }
    .panel-empty { color: #8b949e; font-size: 12px; text-align: center; padding: 32px 0; }

    .diff-meta { font-size: 11px; color: #8b949e; margin-bottom: 6px; display: flex; gap: 12px; }
    .diff-preview {
      font-size: 11px; white-space: pre-wrap; word-break: break-word;
      background: #161b22; border: 1px solid #30363d; border-radius: 4px;
      padding: 8px; color: #e6edf3; max-height: 100%;
    }

    .panel-ai-content { display: flex; flex-direction: column; gap: 8px; }
    .ai-status-bar {
      padding: 4px 8px; border-radius: 4px; font-size: 11px;
      background: #21262d; color: #8b949e;
    }
    .ai-status-bar[data-status="running"] { background: #1f3358; color: #58a6ff; }
    .ai-status-bar[data-status="completed"] { background: #1a3a2a; color: #3fb950; }
    .ai-status-bar[data-status="degraded"] { background: #3d1f1f; color: #ff7b72; }
    .ai-summary { font-size: 12px; color: #e6edf3; }
    .ai-section { margin-top: 6px; }
    .ai-section-title { font-size: 11px; font-weight: 700; color: #8b949e; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.05em; }
    .ai-list { margin: 0; padding-left: 16px; font-size: 12px; }
    .ai-list li { margin-bottom: 2px; }
    .ai-list-risk li { color: #ff7b72; }
    .ai-patch { background: #161b22; border: 1px solid #30363d; border-radius: 4px; padding: 8px; font-size: 11px; white-space: pre-wrap; }
    .ai-idle-hint { color: #8b949e; font-size: 12px; text-align: center; padding: 24px 0; }
  `],
})
export class Diff3EditorComponent implements OnInit, OnDestroy {
  private api = inject(Diff3ApiService);
  private route = inject(ActivatedRoute);
  private destroy$ = new Subject<void>();

  readonly session = signal<Diff3Session | null>(null);
  readonly loading = signal(false);
  readonly error   = signal('');
  readonly aiMode  = signal<AiMode>('review');
  readonly aiRunning = signal(false);
  readonly aiResponse = signal<AiDiffResponse | null>(null);

  readonly panelIds: PanelId[] = ['A', 'B', 'C'];
  readonly aiModes: AiMode[] = AI_MODES;
  readonly aiModeLabels = AI_MODE_LABELS;
  readonly layoutModes: LayoutMode[] = ['equal', 'left-wide', 'right-wide', 'focus-a', 'focus-b', 'focus-c'];
  readonly layoutLabels = LAYOUT_LABELS;

  readonly artifactInputs: Record<PanelId, string> = { A: '', B: '', C: '' };
  readonly filterInputs: Record<PanelId, string>   = { A: '', B: '', C: '' };
  readonly renderModeInputs: Record<PanelId, string> = { A: 'unified', B: 'unified', C: 'unified' };

  private _panelSetups: Record<PanelId, PanelSetup> = { A: 'empty', B: 'empty', C: 'empty' };

  readonly syncScroll = computed(() => {
    return (this.session()?.extensions?.['sync_scroll'] as boolean | undefined) ?? false;
  });

  readonly aiStatus = computed<string>(() => {
    return this.session()?.extensions?.ai_panel_state?.status ?? 'idle';
  });

  readonly aiStatusLabel = computed(() => {
    const s = this.aiStatus();
    const labels: Record<string, string> = {
      running: '⏳ AI analysiert…', completed: '✓ Fertig', degraded: '⚠ Degraded', idle: '',
    };
    return labels[s] ?? s;
  });

  ngOnInit(): void {
    const goalId = this.route.snapshot.queryParamMap.get('goal') ?? undefined;
    this.newSession(goalId);
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  newSession(goalId?: string): void {
    this.loading.set(true);
    this.error.set('');
    this.api.createSession(goalId)
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: s => { this.session.set(s); this.loading.set(false); },
        error: e => { this.error.set(`Session-Fehler: ${e.message ?? e}`); this.loading.set(false); },
      });
  }

  onPanelClick(pid: PanelId): void {
    const s = this.session();
    if (!s) return;
    this.api.setFocus(s.session_id, pid)
      .pipe(takeUntil(this.destroy$))
      .subscribe({ next: upd => this.session.set(upd), error: () => {} });
  }

  onLayoutChange(mode: LayoutMode): void {
    const s = this.session();
    if (!s) return;
    this.api.setLayout(s.session_id, mode)
      .pipe(takeUntil(this.destroy$))
      .subscribe({ next: upd => this.session.set(upd), error: () => {} });
  }

  onSyncToggle(sync: boolean): void {
    const s = this.session();
    if (!s) return;
    this.api.setSync(s.session_id, sync)
      .pipe(takeUntil(this.destroy$))
      .subscribe({ next: upd => this.session.set(upd), error: () => {} });
  }

  onPanelSetupChange(pid: PanelId, setup: PanelSetup): void {
    this._panelSetups[pid] = setup;
    const s = this.session();
    if (!s) return;

    if (setup === 'empty') {
      this.api.updatePanel(s.session_id, pid, { source_kind: 'empty' })
        .pipe(takeUntil(this.destroy$))
        .subscribe({ next: upd => this.session.set(upd), error: e => this.error.set(String(e)) });
    } else if (setup === 'current_diff') {
      this.api.updatePanel(s.session_id, pid, {
        source_kind: 'current_diff',
        path_filter: this.filterInputs[pid],
        render_mode: this.renderModeInputs[pid],
      }).pipe(takeUntil(this.destroy$))
        .subscribe({ next: upd => this.session.set(upd), error: e => this.error.set(String(e)) });
    } else if (setup === 'ai') {
      this.api.updatePanel(s.session_id, pid, { source_kind: 'ai', ai_mode: this.aiMode() })
        .pipe(takeUntil(this.destroy$))
        .subscribe({ next: upd => this.session.set(upd), error: e => this.error.set(String(e)) });
    }
    // output_artifact: wait for user to enter ID
  }

  onCurrentDiffEnter(pid: PanelId): void {
    const s = this.session();
    if (!s) return;
    this.api.updatePanel(s.session_id, pid, {
      source_kind: 'current_diff',
      path_filter: this.filterInputs[pid],
      render_mode: this.renderModeInputs[pid],
    }).pipe(takeUntil(this.destroy$))
      .subscribe({ next: upd => this.session.set(upd), error: e => this.error.set(String(e)) });
  }

  onArtifactEnter(pid: PanelId): void {
    const s = this.session();
    const id = this.artifactInputs[pid].trim();
    if (!s || !id) return;
    this.api.updatePanel(s.session_id, pid, {
      source_kind: 'output_artifact',
      output_artifact_id: id,
    }).pipe(takeUntil(this.destroy$))
      .subscribe({ next: upd => this.session.set(upd), error: e => this.error.set(String(e)) });
  }

  onAiModeSelect(mode: AiMode): void {
    this.aiMode.set(mode);
    const s = this.session();
    if (!s) return;
    this.api.setAiMode(s.session_id, mode)
      .pipe(takeUntil(this.destroy$))
      .subscribe({ next: upd => this.session.set(upd), error: () => {} });
  }

  onRunAi(): void {
    const s = this.session();
    if (!s || this.aiRunning()) return;
    this.aiRunning.set(true);
    this.error.set('');
    this.api.runAi(s.session_id, this.aiMode())
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: result => {
          this.session.set(result.session);
          this.aiResponse.set(result.ai_result?.response ?? null);
          this.aiRunning.set(false);
        },
        error: e => {
          this.error.set(`AI-Fehler: ${e.message ?? e}`);
          this.aiRunning.set(false);
        },
      });
  }

  // ── Panel helpers ──────────────────────────────────────────────────────────

  getPanelSetup(pid: PanelId): PanelSetup {
    return this._panelSetups[pid];
  }

  getPanelType(pid: PanelId): string {
    const panel = this._panel(pid);
    return panel?.panel_type ?? 'empty';
  }

  getPanelRenderMode(pid: PanelId): string {
    return this._panel(pid)?.render_mode ?? '-';
  }

  getPanelSourceLabel(pid: PanelId): string {
    const src = this._panel(pid)?.source_left;
    if (!src) return '–';
    return src.display_name ?? src.source_kind ?? '–';
  }

  getPanelDiffPreview(pid: PanelId): string {
    const src = this._panel(pid)?.source_left;
    if (!src) return '(kein Inhalt)';
    return `[${src.source_kind}] ${src.display_name}\n${JSON.stringify(src.locator, null, 2)}`;
  }

  private _panel(pid: PanelId) {
    return this.session()?.panels.find(p => p.panel_id === pid);
  }
}
