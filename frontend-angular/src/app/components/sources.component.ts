import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';

type SourceItem = {
  source_id: string;
  source_type: string;
  display_name: string;
  trust_level: string;
  enabled: boolean;
  fetch_source: { url?: string; refresh_interval?: string };
  citation_source: { canonical_url?: string; title?: string; license_ref?: string };
  latest_snapshot?: { status?: string; retrieved_at?: string; reason_code?: string; human_message?: string };
};

@Component({
  standalone: true,
  selector: 'app-sources',
  imports: [CommonModule],
  template: `
    <section class="card sources-page">
      <div class="sources-head">
        <div>
          <h2>Knowledge Sources</h2>
          <p class="muted">Keycloak und Wikimedia-Quellen mit Snapshot-Status und Reload.</p>
        </div>
        <button class="btn" (click)="loadSources()" [disabled]="loading">Reload All</button>
      </div>

      @if (loading) {
        <p class="muted">Loading sources ...</p>
      } @else if (error) {
        <p class="error">{{ error }}</p>
      } @else if (!sources.length) {
        <p class="muted">Keine Sources gefunden.</p>
      } @else {
        <div class="source-grid">
          @for (item of sources; track item.source_id) {
            <article class="card card-light source-card">
              <div class="source-card-head">
                <strong>{{ item.display_name }}</strong>
                <span class="badge">{{ item.trust_level }}</span>
              </div>
              <p class="muted id">{{ item.source_id }} · {{ item.source_type }}</p>
              <p class="line">URL: <a [href]="item.fetch_source?.url || '#'" target="_blank" rel="noopener noreferrer">{{ item.fetch_source?.url }}</a></p>
              <p class="line">Snapshot: <strong>{{ item.latest_snapshot?.status || 'none' }}</strong></p>
              <p class="line">Retrieved: {{ item.latest_snapshot?.retrieved_at || '-' }}</p>
              @if (item.latest_snapshot?.reason_code) {
                <p class="error">Reason: {{ item.latest_snapshot?.reason_code }} {{ item.latest_snapshot?.human_message || '' }}</p>
              }
              <div class="source-actions">
                <button class="btn" (click)="refreshSource(item)" [disabled]="isRefreshing(item.source_id)">
                  {{ isRefreshing(item.source_id) ? 'Refreshing ...' : 'Reload' }}
                </button>
                <button class="btn btn-secondary" (click)="loadCitation(item.source_id)">Citation</button>
              </div>
              @if (citations[item.source_id]) {
                <p class="citation">{{ citations[item.source_id] }}</p>
              }
            </article>
          }
        </div>
      }
    </section>
  `,
  styles: [`
    .sources-page { max-width: 1100px; margin: 0 auto; }
    .sources-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .source-grid { margin-top: 16px; display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
    .source-card { display: flex; flex-direction: column; gap: 8px; }
    .source-card-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .badge { border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; font-size: 12px; }
    .id { font-size: 12px; }
    .line { margin: 0; word-break: break-word; }
    .source-actions { display: flex; gap: 8px; }
    .citation { margin: 0; font-size: 12px; color: var(--muted); white-space: pre-wrap; }
    .error { color: #c53030; }
    @media (max-width: 720px) {
      .sources-head { flex-direction: column; align-items: stretch; }
      .source-grid { grid-template-columns: 1fr; }
    }
  `],
})
export class SourcesComponent implements OnInit {
  private http = inject(HttpClient);
  sources: SourceItem[] = [];
  loading = false;
  error = '';
  refreshing = new Set<string>();
  citations: Record<string, string> = {};

  ngOnInit(): void {
    this.loadSources();
  }

  loadSources(): void {
    this.loading = true;
    this.error = '';
    this.http.get<any>('/sources').subscribe({
      next: (payload) => {
        const data = Array.isArray(payload?.data) ? payload.data : [];
        this.sources = data as SourceItem[];
        this.loading = false;
      },
      error: (err) => {
        this.error = String(err?.error?.error || err?.message || 'sources_load_failed');
        this.loading = false;
      },
    });
  }

  isRefreshing(sourceId: string): boolean {
    return this.refreshing.has(sourceId);
  }

  refreshSource(item: SourceItem): void {
    this.refreshing.add(item.source_id);
    this.http.post<any>(`/sources/${encodeURIComponent(item.source_id)}/refresh`, {}).subscribe({
      next: () => {
        this.refreshing.delete(item.source_id);
        this.loadSources();
      },
      error: (err) => {
        this.error = String(err?.error?.error || err?.message || 'source_refresh_failed');
        this.refreshing.delete(item.source_id);
      },
    });
  }

  loadCitation(sourceId: string): void {
    this.http.get<any>(`/sources/${encodeURIComponent(sourceId)}/citation`).subscribe({
      next: (payload) => {
        this.citations[sourceId] = String(payload?.data?.human_readable || '');
      },
      error: (err) => {
        this.citations[sourceId] = String(err?.error?.error || err?.message || 'citation_failed');
      },
    });
  }
}

