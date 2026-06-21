import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  OnDestroy,
  inject,
  signal,
  computed,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';

import { SearchService } from '../services/search.service';
import { ChSearchMode, ChSearchResult, ChExplanationReadModel } from '../models/codehug.models';

/**
 * SearchAndExplainComponent — CH-007.
 *
 * 3 Modi: fulltext, symbol, fuzzy, hybrid (default).
 * Live-Suche mit 180ms Debounce.
 * Treffer oeffnen Detail-Drawer mit heuristischer + optionaler LLM-Erklaerung.
 */
@Component({
  selector: 'ch-search-explain',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-search">
      <header class="ch-search-head">
        <h4>Suche & Erklaerung</h4>
        <p class="ch-muted">{{ resultCount() }} Treffer fuer "{{ currentQuery() }}"</p>
      </header>

      <div class="ch-search-bar">
        <input
          type="search"
          class="ch-search-input"
          [value]="currentQuery()"
          (input)="onQueryChange($any($event.target).value)"
          placeholder="Symbol, Funktion, Datei oder Volltext suchen…"
          data-testid="ch-search-input" />
        <select
          class="ch-search-mode"
          [value]="currentMode()"
          (change)="onModeChange($any($event.target).value)">
          <option value="hybrid">Hybrid</option>
          <option value="fulltext">Volltext</option>
          <option value="symbol">Symbol</option>
          <option value="fuzzy">Fuzzy</option>
        </select>
      </div>

      <ul class="ch-search-results" data-testid="ch-search-results">
        @for (r of results(); track r.symbolId) {
          <li class="ch-search-result" (click)="onSelect(r)" tabindex="0"
              (keydown.enter)="onSelect(r)" (keydown.space)="$event.preventDefault(); onSelect(r)">
            <header class="ch-search-result-head">
              <span class="ch-search-kind" [attr.data-kind]="r.kind">{{ r.kind }}</span>
              <strong>{{ r.name }}</strong>
              <span class="ch-search-match">{{ r.matchMode }}</span>
              <span class="ch-search-score">{{ r.score.toFixed(2) }}</span>
            </header>
            <p class="ch-search-path">{{ r.filePath }}:{{ r.line }}</p>
            <pre class="ch-search-snippet">{{ r.snippet }}</pre>
            @if (r.tags && r.tags.length > 0) {
              <p class="ch-search-tags">
                @for (t of r.tags; track t) { <span class="ch-tag">{{ t }}</span> }
              </p>
            }
          </li>
        }
        @if (currentQuery().length >= 2 && results().length === 0) {
          <li class="ch-search-empty">Keine Treffer.</li>
        }
      </ul>

      @if (selectedResult(); as r) {
        <aside class="ch-explain" aria-label="Symbol-Erklaerung">
          <header class="ch-explain-head">
            <h5>{{ r.name }}</h5>
            <button type="button" class="ch-btn" (click)="onClose()">Schliessen</button>
          </header>
          @if (loadingExplanation()) {
            <p class="ch-muted">Erklaerung wird geladen…</p>
          } @else if (explanation(); as e) {
            <p class="ch-explain-summary" [attr.data-kind]="e.kind">
              <strong>{{ e.kind === 'heuristic' ? 'Heuristik' : e.kind === 'llm' ? 'LLM' : 'Hybrid' }}:</strong>
              {{ e.summary }}
            </p>
            <ul class="ch-explain-details">
              @for (d of e.details; track $index) {
                <li>{{ d }}</li>
              }
            </ul>
            @if (e.relatedSymbols.length > 0) {
              <p class="ch-explain-related">
                <strong>Verwandt:</strong>
                @for (s of e.relatedSymbols; track s) {
                  <span class="ch-related">{{ s }}</span>
                }
              </p>
            }
          } @else if (explanationError(); as err) {
            <p class="ch-error">{{ err }}</p>
          }
        </aside>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .ch-search { display: grid; gap: 10px; }
    .ch-search-head h4 { margin: 0; font-size: 14px; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 0; }
    .ch-error { color: #b91c1c; font-size: 12px; margin: 0; }

    .ch-search-bar { display: grid; grid-template-columns: 1fr 110px; gap: 6px; }
    .ch-search-input, .ch-search-mode {
      padding: 5px 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      font-size: 13px;
    }

    .ch-search-results { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; max-height: 360px; overflow: auto; }
    .ch-search-result {
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--card-bg);
      cursor: pointer;
      transition: background 0.1s;
    }
    .ch-search-result:hover, .ch-search-result:focus {
      background: color-mix(in srgb, var(--accent) 8%, var(--card-bg));
      outline: none;
    }
    .ch-search-result-head { display: flex; gap: 6px; align-items: baseline; }
    .ch-search-kind {
      font-size: 10px;
      padding: 1px 6px;
      background: color-mix(in srgb, var(--accent) 22%, transparent);
      border-radius: 3px;
    }
    .ch-search-kind[data-kind="file"] { background: color-mix(in srgb, #6b7280 30%, transparent); }
    .ch-search-match { font-size: 10px; color: var(--muted); }
    .ch-search-score { font-size: 10px; color: var(--muted); margin-left: auto; }
    .ch-search-path { font-size: 10px; color: var(--muted); margin: 2px 0; font-family: var(--mono, monospace); }
    .ch-search-snippet {
      margin: 2px 0;
      padding: 3px 6px;
      background: var(--bg);
      border-radius: 3px;
      font-size: 11px;
      font-family: var(--mono, monospace);
      max-height: 80px;
      overflow: auto;
      white-space: pre-wrap;
    }
    .ch-search-tags { display: flex; gap: 3px; margin: 2px 0; flex-wrap: wrap; }
    .ch-tag { font-size: 9px; padding: 0 5px; background: var(--bg); border-radius: 3px; }
    .ch-search-empty { color: var(--muted); font-size: 12px; padding: 6px; }

    .ch-explain {
      margin-top: 8px;
      padding: 8px 10px;
      background: var(--card-bg);
      border: 1px solid var(--accent);
      border-radius: 4px;
    }
    .ch-explain-head { display: flex; justify-content: space-between; align-items: baseline; }
    .ch-explain-head h5 { margin: 0; font-size: 13px; }
    .ch-explain-summary { font-size: 12px; margin: 6px 0; }
    .ch-explain-summary[data-kind="llm"] {
      background: color-mix(in srgb, var(--accent) 8%, transparent);
      padding: 4px 6px;
      border-radius: 4px;
    }
    .ch-explain-details { list-style: square; padding-left: 18px; font-size: 11px; color: var(--muted); margin: 4px 0; }
    .ch-explain-related { font-size: 11px; margin: 4px 0; }
    .ch-related {
      display: inline-block;
      margin: 0 4px 0 0;
      padding: 1px 6px;
      font-family: var(--mono, monospace);
      background: var(--bg);
      border-radius: 3px;
      font-size: 10px;
    }

    .ch-btn {
      padding: 3px 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
    }
  `]
})
export class SearchAndExplainComponent implements OnInit, OnDestroy {
  readonly svc = inject(SearchService);
  private readonly destroy$ = new Subject<void>();

  readonly currentQuery = signal('');
  readonly currentMode = signal<ChSearchMode>('hybrid');
  readonly results = signal<ChSearchResult[]>([]);
  readonly selectedResult = signal<ChSearchResult | null>(null);
  readonly explanation = signal<ChExplanationReadModel | null>(null);
  readonly loadingExplanation = signal(false);
  readonly explanationError = signal<string | null>(null);

  readonly resultCount = computed(() => this.results().length);

  ngOnInit(): void {
    this.svc.liveResults()
      .pipe(takeUntil(this.destroy$))
      .subscribe(list => this.results.set(list));
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  onQueryChange(q: string): void {
    this.currentQuery.set(q);
    this.svc.setQuery(q);
  }

  onModeChange(m: ChSearchMode): void {
    this.currentMode.set(m);
    this.svc.setMode(m);
  }

  onSelect(r: ChSearchResult): void {
    this.selectedResult.set(r);
    this.loadingExplanation.set(true);
    this.explanation.set(null);
    this.explanationError.set(null);
    this.svc.explain(r.symbolId).subscribe({
      next: e => {
        this.explanation.set(e);
        this.loadingExplanation.set(false);
      },
      error: err => {
        this.explanationError.set(err.message ?? 'Erklaerung fehlgeschlagen');
        this.loadingExplanation.set(false);
      },
    });
  }

  onClose(): void {
    this.selectedResult.set(null);
    this.explanation.set(null);
  }
}