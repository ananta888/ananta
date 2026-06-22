import {
  Component, OnInit, OnDestroy, inject, signal, computed, ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Subject, switchMap } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import {
  Diff3ApiService, Diff3Session, DiffPanel, AiMode, PanelId, LayoutMode, SourceKind,
  AiDiffResponse, PanelContent,
} from './diff3-api.service';

type PanelSetup = 'empty' | 'current_diff' | 'output_artifact' | 'ai';

interface DiffLine { text: string; cls: string; }

const AI_MODES: AiMode[] = ['review', 'explain', 'risk', 'tests', 'patch', 'chat'];

const AI_MODE_LABELS: Record<AiMode, string> = {
  review: 'Review', explain: 'Explain', risk: 'Risk', tests: 'Tests', patch: 'Patch', chat: 'Chat',
};

const LAYOUT_LABELS: Record<LayoutMode, string> = {
  equal: 'Gleich', 'left-wide': 'Links breit', 'right-wide': 'Rechts breit',
  focus: 'Fokus', compact: 'Kompakt',
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
      <select class="toolbar-select" [ngModel]="session()?.layout_mode" (ngModelChange)="onLayoutChange($event)"
              [disabled]="!session()">
        <option *ngFor="let lm of layoutModes" [value]="lm">{{ layoutLabels[lm] }}</option>
      </select>

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
          <button class="btn-xs btn-reload" title="Neu laden"
                  [disabled]="panelLoadings()[pid]"
                  *ngIf="getPanelSetup(pid) !== 'empty' && getPanelSetup(pid) !== 'ai'"
                  (click)="$event.stopPropagation(); fetchPanelContent(pid)">↺</button>
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

      <!-- Source inputs -->
      <ng-container [ngSwitch]="getPanelSetup(pid)">

        <div *ngSwitchCase="'output_artifact'" class="panel-source-input" (click)="$event.stopPropagation()">
          <input class="source-input" type="text"
                 placeholder="Output-Artifact-ID…"
                 [ngModel]="artifactInputs[pid]"
                 (ngModelChange)="artifactInputs[pid] = $event"
                 (keydown.enter)="onArtifactEnter(pid)">
          <button class="btn-xs" (click)="onArtifactEnter(pid)">Laden</button>
        </div>

        <div *ngSwitchCase="'current_diff'" class="panel-source-input" (click)="$event.stopPropagation()">
          <input class="source-input" type="text"
                 placeholder="Pfad-Filter (optional)"
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
            Kein Inhalt. Wähle eine Quelle oben.
          </div>

          <ng-container *ngSwitchCase="'current_diff'">
            <div class="panel-loading-bar" *ngIf="panelLoadings()[pid]">Lade…</div>
            <ng-container *ngIf="!panelLoadings()[pid]">
              <div class="diff-meta" *ngIf="panelContents()[pid] as c">
                <span>{{ c.ok ? getPanelSourceLabel(pid) : ('Fehler: ' + c.reason_code) }}</span>
                <span *ngIf="c.ok">{{ getPanelRenderMode(pid) }}</span>
                <span *ngIf="c.patch" class="diff-stat">{{ diffStats(c.patch) }}</span>
              </div>
              <div class="diff-preview" *ngIf="getDiffLines(pid) as lines">
                <div *ngFor="let line of lines" [class]="'diff-line ' + line.cls">{{ line.text }}</div>
                <div *ngIf="lines.length === 0" class="panel-empty">Kein Diff (Working Tree sauber oder kein Filter-Treffer).</div>
              </div>
            </ng-container>
          </ng-container>

          <ng-container *ngSwitchCase="'output_artifact'">
            <div class="panel-loading-bar" *ngIf="panelLoadings()[pid]">Lade…</div>
            <ng-container *ngIf="!panelLoadings()[pid]">
              <div class="diff-meta" *ngIf="panelContents()[pid] as c">
                <span>{{ c.ok ? ('Artifact: ' + getPanelSourceLabel(pid)) : ('Fehler: ' + c.reason_code) }}</span>
              </div>
              <div class="diff-preview" *ngIf="getDiffLines(pid) as lines">
                <div *ngFor="let line of lines" [class]="'diff-line ' + line.cls">{{ line.text }}</div>
                <div *ngIf="lines.length === 0" class="panel-empty">Kein Inhalt.</div>
              </div>
            </ng-container>
          </ng-container>

          <div *ngSwitchCase="'ai'" class="panel-ai-content">
            <div class="ai-status-bar" *ngIf="aiStatus() !== 'idle'" [attr.data-status]="aiStatus()">
              {{ aiStatusLabel() }}
            </div>
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
                <div class="diff-preview">
                  <div *ngFor="let line of patchLines()" [class]="'diff-line ' + line.cls">{{ line.text }}</div>
                </div>
              </div>
            </ng-container>
            <div class="ai-idle-hint" *ngIf="aiStatus() === 'idle' && !aiResponse()">
              Wähle einen Modus und klicke "▶ Run AI"
            </div>
          </div>

        </ng-container>
      </div>

    </div>
  </div>

</div>
  `,
  styles: [`
    .diff3-root {
      display: flex; flex-direction: column; height: 100%; min-height: 0;
      background: #0d1117; color: #e6edf3;
      font-family: ui-monospace, 'JetBrains Mono', monospace; font-size: 13px;
    }
    .diff3-toolbar {
      display: flex; align-items: center; justify-content: space-between;
      padding: 6px 12px; background: #161b22; border-bottom: 1px solid #30363d;
      gap: 8px; flex-shrink: 0;
    }
    .toolbar-left, .toolbar-center, .toolbar-right { display: flex; align-items: center; gap: 8px; }
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
    .btn-sm:disabled, .btn-xs:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn-ghost { background: transparent; }
    .diff3-error {
      padding: 6px 12px; background: #3d1f1f; color: #ff7b72;
      border-bottom: 1px solid #6f1919; font-size: 12px; flex-shrink: 0;
    }
    .diff3-loading { padding: 12px; color: #8b949e; text-align: center; }
    .panel-loading-bar { padding: 4px 10px; font-size: 11px; color: #8b949e; }

    /* Panels */
    .diff3-panels { display: flex; flex: 1; min-height: 0; gap: 1px; background: #30363d; }
    .diff3-panel {
      display: flex; flex-direction: column; flex: 1; min-width: 0;
      background: #0d1117; cursor: pointer; transition: outline 0.1s;
    }
    .diff3-panel.active { outline: 1px solid #58a6ff; }

    /* Layout modes */
    [data-layout="left-wide"] .diff3-panel[data-panel="A"] { flex: 2; }
    [data-layout="right-wide"] .diff3-panel[data-panel="C"] { flex: 2; }
    [data-layout="focus-a"] .diff3-panel[data-panel="B"],
    [data-layout="focus-a"] .diff3-panel[data-panel="C"] { flex: 0 0 0; overflow: hidden; }
    [data-layout="focus-b"] .diff3-panel[data-panel="A"],
    [data-layout="focus-b"] .diff3-panel[data-panel="C"] { flex: 0 0 0; overflow: hidden; }
    [data-layout="focus-c"] .diff3-panel[data-panel="A"],
    [data-layout="focus-c"] .diff3-panel[data-panel="B"] { flex: 0 0 0; overflow: hidden; }

    /* Panel header */
    .panel-header {
      display: flex; align-items: center; gap: 8px;
      padding: 5px 10px; background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0;
    }
    .panel-label { font-weight: 700; color: #58a6ff; }
    .panel-type { font-size: 11px; color: #8b949e; flex: 1; }
    .panel-controls { display: flex; gap: 4px; align-items: center; }
    .panel-select {
      background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
      border-radius: 3px; padding: 1px 4px; font-size: 11px; cursor: pointer;
    }
    .panel-select-sm {
      font-size: 11px; padding: 1px 3px; background: #0d1117;
      color: #e6edf3; border: 1px solid #30363d; border-radius: 3px;
    }

    /* Source inputs */
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
    .btn-xs:hover:not(:disabled) { background: #30363d; }
    .btn-reload { padding: 2px 6px; font-size: 13px; line-height: 1; }

    /* AI controls */
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

    /* Panel body + diff display */
    .panel-body { flex: 1; overflow-y: auto; padding: 6px 0; min-height: 0; }
    .panel-empty { color: #8b949e; font-size: 12px; text-align: center; padding: 32px 10px; }

    .diff-meta {
      font-size: 11px; color: #8b949e; padding: 2px 10px 4px;
      display: flex; gap: 12px; flex-shrink: 0;
    }
    .diff-stat { color: #3fb950; }

    .diff-preview {
      font-size: 11.5px; overflow-x: auto;
      border-top: 1px solid #21262d;
    }
    .diff-line {
      display: block; padding: 0 10px; white-space: pre; font-family: inherit;
      line-height: 1.55; min-height: 1.55em;
    }
    .ln-add    { background: #1a3a2a; color: #3fb950; }
    .ln-remove { background: #3d1f1f; color: #ff7b72; }
    .ln-hunk   { background: #1a2744; color: #79c0ff; }
    .ln-meta   { color: #6e7681; }
    .ln-normal { color: #e6edf3; }

    /* AI panel */
    .panel-ai-content { display: flex; flex-direction: column; gap: 8px; padding: 8px 10px; }
    .ai-status-bar {
      padding: 4px 8px; border-radius: 4px; font-size: 11px; background: #21262d; color: #8b949e;
    }
    .ai-status-bar[data-status="running"] { background: #1f3358; color: #58a6ff; }
    .ai-status-bar[data-status="completed"] { background: #1a3a2a; color: #3fb950; }
    .ai-status-bar[data-status="degraded"] { background: #3d1f1f; color: #ff7b72; }
    .ai-summary { font-size: 12px; color: #e6edf3; }
    .ai-section { margin-top: 6px; }
    .ai-section-title {
      font-size: 11px; font-weight: 700; color: #8b949e; margin-bottom: 3px;
      text-transform: uppercase; letter-spacing: 0.05em;
    }
    .ai-list { margin: 0; padding-left: 16px; font-size: 12px; }
    .ai-list li { margin-bottom: 2px; }
    .ai-list-risk li { color: #ff7b72; }
    .ai-idle-hint { color: #8b949e; font-size: 12px; text-align: center; padding: 24px 0; }
  `],
})
export class Diff3EditorComponent implements OnInit, OnDestroy {
  private api     = inject(Diff3ApiService);
  private route   = inject(ActivatedRoute);
  private destroy$ = new Subject<void>();

  readonly session     = signal<Diff3Session | null>(null);
  readonly loading     = signal(false);
  readonly error       = signal('');
  readonly aiMode      = signal<AiMode>('review');
  readonly aiRunning   = signal(false);
  readonly aiResponse  = signal<AiDiffResponse | null>(null);

  readonly panelContents = signal<Record<PanelId, PanelContent | null>>({ A: null, B: null, C: null });
  readonly panelLoadings = signal<Record<PanelId, boolean>>({ A: false, B: false, C: false });

  readonly panelIds: PanelId[]    = ['A', 'B', 'C'];
  readonly aiModes: AiMode[]      = AI_MODES;
  readonly aiModeLabels           = AI_MODE_LABELS;
  readonly layoutModes: LayoutMode[] = ['equal', 'left-wide', 'right-wide', 'focus-a', 'focus-b', 'focus-c'];
  readonly layoutLabels           = LAYOUT_LABELS;

  readonly artifactInputs: Record<PanelId, string>   = { A: '', B: '', C: '' };
  readonly filterInputs: Record<PanelId, string>     = { A: '', B: '', C: '' };
  readonly renderModeInputs: Record<PanelId, string> = { A: 'unified', B: 'unified', C: 'unified' };

  private _panelSetups: Record<PanelId, PanelSetup> = { A: 'current_diff', B: 'empty', C: 'empty' };

  readonly syncScroll = computed(() =>
    (this.session()?.extensions?.['sync_scroll'] as boolean | undefined) ?? false
  );

  readonly aiStatus = computed<string>(() =>
    this.session()?.extensions?.ai_panel_state?.status ?? 'idle'
  );

  readonly aiStatusLabel = computed(() => {
    const labels: Record<string, string> = {
      running: '⏳ AI analysiert…', completed: '✓ Fertig', degraded: '⚠ Degraded', idle: '',
    };
    return labels[this.aiStatus()] ?? this.aiStatus();
  });

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  ngOnInit(): void {
    const goalId = this.route.snapshot.queryParamMap.get('goal') ?? undefined;
    this._createAndInitSession(goalId);
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  // ── Session ─────────────────────────────────────────────────────────────────

  newSession(goalId?: string): void {
    this.panelContents.set({ A: null, B: null, C: null });
    this._panelSetups = { A: 'current_diff', B: 'empty', C: 'empty' };
    this.aiResponse.set(null);
    this._createAndInitSession(goalId);
  }

  private _createAndInitSession(goalId?: string): void {
    this.loading.set(true);
    this.error.set('');
    // Backend already pre-populates panel A with current_diff; we just fetch its content.
    this.api.createSession(goalId)
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: s => {
          this.session.set(s);
          this.loading.set(false);
          this.fetchPanelContent('A');
        },
        error: e => { this.error.set(`Session-Fehler: ${e.message ?? e}`); this.loading.set(false); },
      });
  }

  // ── Panel content ────────────────────────────────────────────────────────────

  fetchPanelContent(pid: PanelId): void {
    const s = this.session();
    if (!s) return;
    this.panelLoadings.update(l => ({ ...l, [pid]: true }));
    this.api.getPanelContent(s.session_id, pid)
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: c  => { this.panelContents.update(m => ({ ...m, [pid]: c })); this.panelLoadings.update(l => ({ ...l, [pid]: false })); },
        error: () => { this.panelLoadings.update(l => ({ ...l, [pid]: false })); },
      });
  }

  // ── Toolbar ──────────────────────────────────────────────────────────────────

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

  // ── Panel setup ──────────────────────────────────────────────────────────────

  onPanelSetupChange(pid: PanelId, setup: PanelSetup): void {
    this._panelSetups[pid] = setup;
    this.panelContents.update(m => ({ ...m, [pid]: null }));
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
        .subscribe({
          next: upd => { this.session.set(upd); this.fetchPanelContent(pid); },
          error: e => this.error.set(String(e)),
        });
    } else if (setup === 'ai') {
      this.api.updatePanel(s.session_id, pid, { source_kind: 'ai', ai_mode: this.aiMode() })
        .pipe(takeUntil(this.destroy$))
        .subscribe({ next: upd => this.session.set(upd), error: e => this.error.set(String(e)) });
    }
  }

  onCurrentDiffEnter(pid: PanelId): void {
    const s = this.session();
    if (!s) return;
    this.api.updatePanel(s.session_id, pid, {
      source_kind: 'current_diff',
      path_filter: this.filterInputs[pid],
      render_mode: this.renderModeInputs[pid],
    }).pipe(takeUntil(this.destroy$))
      .subscribe({
        next: upd => { this.session.set(upd); this.fetchPanelContent(pid); },
        error: e => this.error.set(String(e)),
      });
  }

  onArtifactEnter(pid: PanelId): void {
    const s = this.session();
    const id = this.artifactInputs[pid].trim();
    if (!s || !id) return;
    this.api.updatePanel(s.session_id, pid, {
      source_kind: 'output_artifact',
      output_artifact_id: id,
    }).pipe(takeUntil(this.destroy$))
      .subscribe({
        next: upd => { this.session.set(upd); this.fetchPanelContent(pid); },
        error: e => this.error.set(String(e)),
      });
  }

  // ── AI ───────────────────────────────────────────────────────────────────────

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
        error: e => { this.error.set(`AI-Fehler: ${e.message ?? e}`); this.aiRunning.set(false); },
      });
  }

  // ── Panel helpers ────────────────────────────────────────────────────────────

  getPanelSetup(pid: PanelId): PanelSetup { return this._panelSetups[pid]; }

  getPanelType(pid: PanelId): string { return this._panel(pid)?.panel_type ?? 'empty'; }

  getPanelRenderMode(pid: PanelId): string { return this._panel(pid)?.render_mode ?? '-'; }

  getPanelSourceLabel(pid: PanelId): string {
    const src = this._panel(pid)?.source_left;
    if (!src) return '–';
    return src.display_name ?? src.source_kind ?? '–';
  }

  getDiffLines(pid: PanelId): DiffLine[] {
    const c = this.panelContents()[pid];
    if (!c || !c.ok) return [];
    const raw = c.patch ?? c.text ?? (
      c.left_text && c.right_text
        ? `=== ${c.left_ref ?? 'Links'} ===\n${c.left_text}\n\n=== ${c.right_ref ?? 'Rechts'} ===\n${c.right_text}`
        : ''
    );
    if (!raw.trim()) return [];
    return raw.split('\n').map(line => ({ text: line, cls: _lineClass(line) }));
  }

  patchLines(): DiffLine[] {
    const patches = this.aiResponse()?.patch_suggestions ?? [];
    return patches.join('\n').split('\n').map(l => ({ text: l, cls: _lineClass(l) }));
  }

  diffStats(patch: string): string {
    let add = 0, rem = 0;
    for (const line of patch.split('\n')) {
      if (line.startsWith('+') && !line.startsWith('+++')) add++;
      else if (line.startsWith('-') && !line.startsWith('---')) rem++;
    }
    return add || rem ? `+${add} -${rem}` : '';
  }

  private _panel(pid: PanelId): DiffPanel | undefined {
    return this.session()?.panels.find(p => p.panel_id === pid);
  }
}

function _lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-line ln-meta';
  if (line.startsWith('+')) return 'diff-line ln-add';
  if (line.startsWith('-')) return 'diff-line ln-remove';
  if (line.startsWith('@@')) return 'diff-line ln-hunk';
  if (/^(diff |index |new file|deleted file|rename )/.test(line)) return 'diff-line ln-meta';
  return 'diff-line ln-normal';
}
