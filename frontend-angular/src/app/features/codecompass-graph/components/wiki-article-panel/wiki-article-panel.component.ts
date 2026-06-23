import {
  Component, Input, Output, EventEmitter, OnChanges,
  ChangeDetectionStrategy, inject, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { catchError, of } from 'rxjs';

interface WikiSummary {
  title: string;
  extract: string;
  thumbnail?: { source: string; width: number; height: number };
  content_urls?: { desktop?: { page?: string } };
}

@Component({
  standalone: true,
  selector: 'app-wiki-article-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="wap-root">
      <div class="wap-header">
        <span class="wap-title" [title]="title">{{ title }}</span>
        <div class="wap-actions">
          @if (wikiUrl()) {
            <a class="wap-btn-sm" [href]="wikiUrl()!" target="_blank" rel="noopener">↗ Wikipedia</a>
          }
          <button class="wap-close" (click)="closed.emit()">✕</button>
        </div>
      </div>

      @if (loading()) {
        <div class="wap-status">Lade Artikel…</div>
      } @else if (error()) {
        <div class="wap-status wap-error">{{ error() }}</div>
      } @else {
        <div class="wap-body">
          @if (thumbnail()) {
            <img class="wap-thumb" [src]="thumbnail()!" [alt]="title" />
          }
          <div class="wap-extract">{{ extract() }}</div>
          @if (wikiUrl()) {
            <a class="wap-full-link" [href]="wikiUrl()!" target="_blank" rel="noopener">
              Vollständigen Artikel auf Wikipedia lesen →
            </a>
          }
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
    .wap-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
    .wap-btn-sm {
      font-size: 11px; padding: 2px 8px; border-radius: 4px;
      background: #21262d; color: #58a6ff; border: 1px solid #30363d;
      cursor: pointer; text-decoration: none;
    }
    .wap-btn-sm:hover { background: #30363d; }
    .wap-close {
      width: 22px; height: 22px; border-radius: 4px; border: none;
      background: transparent; color: #8b949e; cursor: pointer; font-size: 14px;
      display: flex; align-items: center; justify-content: center;
    }
    .wap-close:hover { background: #21262d; color: #e6edf3; }
    .wap-status { padding: 24px; color: #8b949e; text-align: center; }
    .wap-error { color: #f85149; }
    .wap-body { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .wap-thumb {
      max-width: 160px; max-height: 200px; border-radius: 6px;
      object-fit: cover; align-self: flex-start;
      border: 1px solid #30363d;
    }
    .wap-extract { line-height: 1.7; color: #c9d1d9; white-space: pre-wrap; }
    .wap-full-link {
      color: #58a6ff; text-decoration: none; font-size: 12px;
      padding: 6px 0; border-top: 1px solid #21262d; margin-top: 4px;
    }
    .wap-full-link:hover { text-decoration: underline; }
  `],
})
export class WikiArticlePanelComponent implements OnChanges {
  @Input() nodeId = '';   // e.g. "article:albert-einstein"
  @Input() title = '';    // e.g. "Albert Einstein"
  @Output() closed = new EventEmitter<void>();

  private readonly http = inject(HttpClient);

  readonly loading  = signal(true);
  readonly error    = signal('');
  readonly extract  = signal('');
  readonly thumbnail = signal<string | null>(null);
  readonly wikiUrl  = signal<string | null>(null);

  ngOnChanges(): void {
    if (!this.title) return;
    this._load(this.title);
  }

  private _load(title: string): void {
    this.loading.set(true);
    this.error.set('');
    this.extract.set('');
    this.thumbnail.set(null);
    this.wikiUrl.set(null);

    const url = `https://de.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`;
    this.http.get<WikiSummary>(url).pipe(
      catchError(err => {
        const msg = err?.status === 404 ? 'Artikel nicht gefunden.' : 'Wikipedia nicht erreichbar.';
        return of({ title, extract: msg, thumbnail: undefined, content_urls: undefined } as WikiSummary);
      }),
    ).subscribe(data => {
      this.loading.set(false);
      this.extract.set(data.extract ?? '');
      this.thumbnail.set(data.thumbnail?.source ?? null);
      this.wikiUrl.set(data.content_urls?.desktop?.page ?? `https://de.wikipedia.org/wiki/${encodeURIComponent(title)}`);
    });
  }
}
