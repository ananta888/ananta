import { Component, OnInit } from '@angular/core';
import { forkJoin } from 'rxjs';
import {
  SourceItem,
  SourcePackItem,
  SourceSnapshot,
  SourcesService,
} from '../services/sources.service';
import { SourceImportDialogComponent } from '../features/sources/source-import-dialog.component';
import { SourceCitationPanelComponent } from '../features/sources/source-citation-panel.component';
import { SourceChatPanelComponent } from '../features/sources/source-chat-panel.component';

@Component({
  standalone: true,
  selector: 'app-sources',
  imports: [SourceImportDialogComponent, SourceCitationPanelComponent, SourceChatPanelComponent],
  template: `
    <section class="card sources-page">
      <div class="sources-head">
        <div>
          <h2>Knowledge Sources</h2>
          <p class="muted">Source Packs, Imports, Snapshots, Citations und Source Chat.</p>
        </div>
        <div class="source-actions">
          <button class="btn" (click)="importOpen = true">Quelle importieren</button>
          <button class="btn btn-secondary" (click)="loadSources()" [disabled]="loading">Reload All</button>
        </div>
      </div>

      <app-source-import-dialog [open]="importOpen" (closed)="onImportClosed($event)" />

      <div class="pack-grid">
        @for (pack of sourcePacks; track pack.source_pack_id) {
          <article class="card card-light source-card">
            <strong>{{ pack.display_name }}</strong>
            <span class="badge">{{ pack.version }}</span>
            <p>{{ pack.source_pack_id }} · sources={{ pack.sources.length }}</p>
            <button class="btn" (click)="bootstrapPack(pack.source_pack_id, true)">Bootstrap Dry-Run</button>
            <button class="btn btn-secondary" (click)="bootstrapPack(pack.source_pack_id, false)">Bootstrap</button>
            @if (packReports[pack.source_pack_id]) {
              <p>Pack status: {{ packReports[pack.source_pack_id].status || '-' }}</p>
            }
          </article>
        }
      </div>

      @if (loading) {
        <p>Loading sources ...</p>
      } @else if (error) {
        <p class="error">{{ error }}</p>
      } @else if (!sources.length) {
        <p>Keine Sources gefunden.</p>
        <button class="btn" (click)="importOpen = true">Quelle importieren</button>
      } @else {
        <div class="source-grid">
          @for (item of sources; track item.source_id) {
            <article class="card card-light source-card">
              <div><strong>{{ item.display_name }}</strong> <span class="badge">{{ item.trust_level }}</span></div>
              <p>{{ item.source_id }} · {{ item.source_type }}</p>
              <p>Snapshot: {{ item.latest_snapshot?.status || 'none' }}</p>
              <p>Citation: {{ item.citation_source?.canonical_url ? 'available' : 'missing' }}</p>
              @if (item.latest_snapshot?.reason_code) {
                <p class="error">{{ item.latest_snapshot?.reason_code }} {{ item.latest_snapshot?.human_message }}</p>
              }
              <div class="source-actions">
                <button class="btn" (click)="refreshSource(item)">Reload</button>
                <button class="btn btn-secondary" (click)="loadCitation(item.source_id)">Citation</button>
                <button class="btn btn-secondary" (click)="showDetails(item)">Details</button>
                @if (item.source_type === 'open_notebook') {
                  <button class="btn btn-secondary" (click)="chatSourceId = item.source_id">Chat</button>
                }
              </div>
              @if (citations[item.source_id]) { <pre>{{ citations[item.source_id] }}</pre> }
              @if (selectedSource?.source_id === item.source_id) {
                <app-source-citation-panel [source]="item" [snapshots]="snapshots[item.source_id] || []" />
              }
              @if (chatSourceId === item.source_id) {
                <app-source-chat-panel [sourceId]="item.source_id" />
              }
            </article>
          }
        </div>
      }
    </section>
  `,
  styles: [`
    .sources-page { max-width: 1100px; margin: 0 auto; }
    .sources-head, .source-actions { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .pack-grid, .source-grid { margin-top: 16px; display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
    .source-card { display: flex; flex-direction: column; gap: 8px; }
    .badge { border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; font-size: 12px; }
    .error { color: #c53030; }
    pre { white-space: pre-wrap; }
  `],
})
export class SourcesComponent implements OnInit {
  sources: SourceItem[] = [];
  sourcePacks: SourcePackItem[] = [];
  loading = false;
  error = '';
  importOpen = false;
  selectedSource: SourceItem | null = null;
  chatSourceId = '';
  citations: Record<string, string> = {};
  snapshots: Record<string, SourceSnapshot[]> = {};
  packReports: Record<string, any> = {};

  constructor(private readonly api: SourcesService) {}

  ngOnInit(): void {
    this.loadSources();
  }

  loadSources(): void {
    this.loading = true;
    this.error = '';
    forkJoin({ sources: this.api.listSources(), packs: this.api.listPacks() }).subscribe({
      next: result => {
        this.sources = result.sources;
        this.sourcePacks = result.packs;
        this.loading = false;
      },
      error: error => {
        this.error = String(error?.error?.data?.reason_code || error?.error?.error || error?.message || 'sources_load_failed');
        this.loading = false;
      },
    });
  }

  onImportClosed(imported: boolean): void {
    this.importOpen = false;
    if (imported) this.loadSources();
  }

  bootstrapPack(sourcePackId: string, dryRun: boolean): void {
    this.api.bootstrapPack(sourcePackId, dryRun).subscribe({
      next: report => { this.packReports[sourcePackId] = report; this.loadSources(); },
      error: error => { this.error = String(error?.message || 'source_pack_bootstrap_failed'); },
    });
  }

  refreshSource(item: SourceItem): void {
    this.api.refresh(item.source_id).subscribe({
      next: () => this.loadSources(),
      error: error => { this.error = String(error?.message || 'source_refresh_failed'); },
    });
  }

  loadCitation(sourceId: string): void {
    this.api.citation(sourceId).subscribe({
      next: citation => { this.citations[sourceId] = String(citation.human_readable || citation.long || ''); },
      error: error => { this.citations[sourceId] = String(error?.message || 'citation_failed'); },
    });
  }

  showDetails(item: SourceItem): void {
    this.selectedSource = item;
    this.api.snapshots(item.source_id).subscribe({
      next: snapshots => { this.snapshots[item.source_id] = snapshots.slice(0, 10); },
      error: error => { this.error = String(error?.message || 'snapshots_failed'); },
    });
  }
}
