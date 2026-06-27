import {
  Component, Input, Output, EventEmitter, OnInit, OnChanges, OnDestroy,
  ChangeDetectionStrategy, inject, signal,
} from '@angular/core';

import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { Router } from '@angular/router';
import { Diff3ApiService } from '../../../diff3/diff3-api.service';

type ViewMode = 'diff' | 'file';
interface CodeLine { text: string; cls: string; }

function diffLineClass(line: string): string {
  if (line.startsWith('+')) return 'ln-add';
  if (line.startsWith('-')) return 'ln-remove';
  if (line.startsWith('@@')) return 'ln-hunk';
  if (line.startsWith('diff') || line.startsWith('index') || line.startsWith('---') || line.startsWith('+++')) return 'ln-meta';
  return 'ln-normal';
}

@Component({
  standalone: true,
  selector: 'app-file-diff-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  template: `
    <div class="fdp-root">
      <div class="fdp-header">
        <span class="fdp-path" [title]="filePath">{{ filePath }}</span>
        <div class="fdp-actions">
          <div class="fdp-toggle">
            <button [class.active]="viewMode() === 'diff'"  (click)="switchMode('diff')">Diff</button>
            <button [class.active]="viewMode() === 'file'"  (click)="switchMode('file')">Datei</button>
          </div>
          <button class="fdp-btn-sm" title="Im 3er Diff öffnen" (click)="openFull()">⬡ Vollansicht</button>
          <button class="fdp-close" title="Schließen" (click)="closed.emit()">✕</button>
        </div>
      </div>

      @if (loading()) {
        <div class="fdp-status">Lade…</div>
      } @else if (error()) {
        <div class="fdp-status fdp-error">{{ error() }}</div>
      } @else {
        <div class="fdp-body">
          @for (line of lines(); track $index) {
            <div [class]="'code-line ' + line.cls">{{ line.text }}</div>
          }
          @if (lines().length === 0) {
            <div class="fdp-status">
              @if (viewMode() === 'diff') { Kein Diff (Working Tree sauber). }
              @else { Datei ist leer. }
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100%; min-height: 0; }
    .fdp-root {
      display: flex; flex-direction: column; height: 100%; min-height: 0;
      background: #0d1117; color: #e6edf3;
      font-family: ui-monospace, 'JetBrains Mono', monospace; font-size: 12px;
    }
    .fdp-header {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding: 6px 10px; background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0;
    }
    .fdp-path {
      flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      color: #79c0ff; font-size: 11px; min-width: 0;
    }
    .fdp-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }

    /* Toggle diff/file */
    .fdp-toggle { display: flex; border: 1px solid #30363d; border-radius: 4px; overflow: hidden; }
    .fdp-toggle button {
      padding: 2px 8px; background: #0d1117; color: #8b949e; border: none;
      cursor: pointer; font-size: 11px; font-family: inherit;
    }
    .fdp-toggle button:first-child { border-right: 1px solid #30363d; }
    .fdp-toggle button.active { background: #1f3358; color: #58a6ff; }
    .fdp-toggle button:not(.active):hover { background: #21262d; }

    .fdp-btn-sm {
      padding: 2px 7px; border-radius: 3px; border: 1px solid #30363d;
      background: #21262d; color: #8b949e; cursor: pointer; font-size: 11px; font-family: inherit;
    }
    .fdp-btn-sm:hover { background: #30363d; color: #e6edf3; }
    .fdp-close {
      width: 22px; height: 22px; display: flex; align-items: center; justify-content: center;
      border-radius: 4px; border: 1px solid #30363d; background: transparent;
      color: #8b949e; cursor: pointer; font-size: 14px; line-height: 1; padding: 0;
    }
    .fdp-close:hover { background: #3d1f1f; color: #ff7b72; border-color: #6f1919; }

    .fdp-status { padding: 16px 12px; color: #8b949e; font-style: italic; font-size: 12px; }
    .fdp-error { color: #ff7b72; }
    .fdp-body { flex: 1; overflow-y: auto; overflow-x: auto; padding: 4px 0; min-height: 0; }

    .code-line {
      display: block; padding: 0 10px; white-space: pre; font-family: inherit;
      line-height: 1.55; min-height: 1.55em; font-size: 11.5px;
    }
    /* diff colours */
    .ln-add    { background: #1a3a2a; color: #3fb950; }
    .ln-remove { background: #3d1f1f; color: #ff7b72; }
    .ln-hunk   { background: #1a2744; color: #79c0ff; }
    .ln-meta   { color: #6e7681; }
    .ln-normal { color: #e6edf3; }
    /* plain file */
    .ln-plain  { color: #e6edf3; }
  `],
})
export class FileDiffPanelComponent implements OnInit, OnChanges, OnDestroy {
  @Input() filePath = '';
  @Output() closed = new EventEmitter<void>();

  private readonly api      = inject(Diff3ApiService);
  private readonly router   = inject(Router);
  private readonly destroy$ = new Subject<void>();
  private sessionId = '';

  readonly viewMode  = signal<ViewMode>('diff');
  readonly loading   = signal(true);
  readonly error     = signal('');
  readonly lines     = signal<CodeLine[]>([]);

  private diffLines: CodeLine[] = [];
  private fileLines: CodeLine[] = [];

  ngOnInit(): void {
    this._initSession();
  }

  ngOnChanges(): void {
    if (this.sessionId) {
      this._loadBoth();
    }
  }

  switchMode(mode: ViewMode): void {
    this.viewMode.set(mode);
    this.lines.set(mode === 'diff' ? this.diffLines : this.fileLines);
  }

  openFull(): void {
    this.router.navigate(['/diff3'], { queryParams: { file: this.filePath } });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
    if (this.sessionId) {
      this.api.deleteSession(this.sessionId).subscribe();
    }
  }

  private _initSession(): void {
    this.loading.set(true);
    this.error.set('');
    this.api.createSession().pipe(takeUntil(this.destroy$)).subscribe({
      next: s => {
        this.sessionId = s.session_id;
        this._loadBoth();
      },
      error: e => { this.loading.set(false); this.error.set(String(e)); },
    });
  }

  private _loadBoth(): void {
    this.loading.set(true);
    this.error.set('');
    const sid = this.sessionId;

    // Panel A = diff, Panel B = full file
    this.api.updatePanel(sid, 'A', { source_kind: 'current_diff', path_filter: this.filePath })
      .pipe(takeUntil(this.destroy$)).subscribe({
        next: () => this.api.getPanelContent(sid, 'A').pipe(takeUntil(this.destroy$)).subscribe({
          next: c => {
            const raw = c.ok ? (c.patch ?? c.text ?? '') : '';
            this.diffLines = raw.trim()
              ? raw.split('\n').map(l => ({ text: l, cls: diffLineClass(l) }))
              : [];
            this._loadFileContent(sid);
          },
          error: () => this._loadFileContent(sid),
        }),
        error: () => this._loadFileContent(sid),
      });
  }

  private _loadFileContent(sid: string): void {
    this.api.updatePanel(sid, 'B', { source_kind: 'file_content', path: this.filePath })
      .pipe(takeUntil(this.destroy$)).subscribe({
        next: () => this.api.getPanelContent(sid, 'B').pipe(takeUntil(this.destroy$)).subscribe({
          next: c => {
            const raw = c.ok ? (c.text ?? '') : '';
            this.fileLines = raw.split('\n').map(l => ({ text: l, cls: 'ln-plain' }));
            this.loading.set(false);
            this.lines.set(this.viewMode() === 'diff' ? this.diffLines : this.fileLines);
          },
          error: () => {
            this.fileLines = [];
            this.loading.set(false);
            this.lines.set(this.viewMode() === 'diff' ? this.diffLines : this.fileLines);
          },
        }),
        error: () => {
          this.fileLines = [];
          this.loading.set(false);
          this.lines.set(this.diffLines);
        },
      });
  }
}
