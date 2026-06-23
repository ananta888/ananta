import {
  Component, Input, Output, EventEmitter, OnChanges,
  ChangeDetectionStrategy, inject, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { InternalsService } from '../../../../features/codehug/services/internals.service';

@Component({
  standalone: true,
  selector: 'app-wiki-article-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="wap-root">
      <div class="wap-header">
        <span class="wap-title" [title]="title">{{ title }}</span>
        <button class="wap-close" (click)="closed.emit()">✕</button>
      </div>

      @if (loading()) {
        <div class="wap-status">Lade Artikel…</div>
      } @else if (status() === 'not_built') {
        <div class="wap-not-built">
          <p class="wap-hint">
            Für lokale Artikelinhalte muss einmalig ein Inhaltsindex aus
            <code>details.jsonl</code> aufgebaut werden (~3–5 Min.).
          </p>
          <button class="wap-btn" [disabled]="building()" (click)="startBuild()">
            @if (building()) { <span class="wap-spin"></span> Wird aufgebaut… }
            @else { Inhaltsindex aufbauen }
          </button>
        </div>
      } @else if (status() === 'not_found') {
        <div class="wap-status wap-muted">Artikel nicht im lokalen Index gefunden.</div>
      } @else if (status() === 'error') {
        <div class="wap-status wap-error">Fehler: {{ errorMsg() }}</div>
      } @else if (status() === 'found') {
        <div class="wap-body">
          <div class="wap-text">{{ intro() }}</div>
        </div>
      }
    </div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100%; }
    .wap-root { display: flex; flex-direction: column; height: 100%; background: #0d1117; color: #e6edf3; font-size: 13px; }
    .wap-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 8px 12px; background: #161b22; border-bottom: 1px solid #30363d;
      flex-shrink: 0; gap: 8px;
    }
    .wap-title { font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
    .wap-close {
      width: 22px; height: 22px; border-radius: 4px; border: none;
      background: transparent; color: #8b949e; cursor: pointer; font-size: 14px;
      display: flex; align-items: center; justify-content: center; flex-shrink: 0;
    }
    .wap-close:hover { background: #21262d; color: #e6edf3; }
    .wap-status { padding: 24px; color: #8b949e; text-align: center; font-size: 13px; }
    .wap-muted { color: #8b949e; }
    .wap-error { color: #f85149; }
    .wap-not-built { padding: 20px 16px; display: flex; flex-direction: column; gap: 12px; }
    .wap-hint { margin: 0; font-size: 12px; color: #8b949e; line-height: 1.6; }
    .wap-btn {
      align-self: flex-start; padding: 6px 14px; font-size: 12px; border-radius: 6px;
      background: #21262d; color: #c9d1d9; border: 1px solid #30363d; cursor: pointer;
      display: inline-flex; align-items: center; gap: 6px;
    }
    .wap-btn:hover:not([disabled]) { background: #30363d; }
    .wap-btn[disabled] { opacity: .6; cursor: default; }
    .wap-spin {
      width: 12px; height: 12px; border: 2px solid #58a6ff; border-top-color: transparent;
      border-radius: 50%; animation: spin .7s linear infinite; flex-shrink: 0;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .wap-body { flex: 1; overflow-y: auto; padding: 16px; }
    .wap-text { line-height: 1.75; color: #c9d1d9; white-space: pre-wrap; font-size: 13px; }
  `],
})
export class WikiArticlePanelComponent implements OnChanges {
  @Input() nodeId = '';    // e.g. "article:albert-einstein"
  @Input() title  = '';
  @Input() indexId = '';
  @Output() closed = new EventEmitter<void>();

  private readonly svc = inject(InternalsService);

  readonly loading  = signal(true);
  readonly status   = signal<'idle' | 'not_built' | 'not_found' | 'found' | 'error'>('idle');
  readonly intro    = signal('');
  readonly errorMsg = signal('');
  readonly building = signal(false);

  private _pollTimer: ReturnType<typeof setTimeout> | null = null;

  ngOnChanges(): void {
    if (this._pollTimer) { clearTimeout(this._pollTimer); this._pollTimer = null; }
    if (!this.nodeId || !this.indexId) return;
    this._load();
  }

  startBuild(): void {
    if (!this.indexId) return;
    this.building.set(true);
    this.svc.buildWikiContent(this.indexId).subscribe(() => this._pollBuild());
  }

  private _load(): void {
    const slug = this.nodeId.startsWith('article:') ? this.nodeId.slice('article:'.length) : this.nodeId;
    this.loading.set(true);
    this.status.set('idle');
    this.svc.getWikiArticleContent(this.indexId, slug).subscribe(data => {
      this.loading.set(false);
      const st = data?.status ?? 'error';
      this.status.set(st as any);
      if (st === 'found') this.intro.set(data.intro ?? '');
      if (st === 'error') this.errorMsg.set(data.error ?? 'unbekannter Fehler');
    });
  }

  private _pollBuild(): void {
    this.svc.getWikiContentStatus(this.indexId).subscribe(data => {
      if (data?.status === 'building') {
        this._pollTimer = setTimeout(() => this._pollBuild(), 4000);
      } else {
        this.building.set(false);
        this._load();
      }
    });
  }
}
