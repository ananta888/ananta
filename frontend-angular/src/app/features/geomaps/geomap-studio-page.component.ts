import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ExplanationNoticeComponent } from '../../shared/ui/display';
import { PageIntroComponent, SectionCardComponent } from '../../shared/ui/layout';
import { GeoMapApiService } from './geomap-api.service';
import { GeoMapCsvParser } from './geomap-csv-parser.service';
import { GeoMapDownloadService } from './geomap-download.service';
import { GeoMapDraftStore } from './geomap-draft.store';
import {
  GeoMapAggregation,
  GeoMapCatalog,
  GeoMapDraft,
  GeoMapGeometry,
  GeoMapProjection,
} from './geomap.models';
import { GeoMapRendererComponent } from './geomap-renderer.component';

@Component({
  selector: 'app-geomap-studio-page',
  standalone: true,
  imports: [
    CommonModule,
    ExplanationNoticeComponent,
    FormsModule,
    GeoMapRendererComponent,
    PageIntroComponent,
    SectionCardComponent,
  ],
  template: `
    <app-page-intro
      title="GeoMap Studio"
      subtitle="Offline-Karten aus CSV- und Tabellenwerten mit überprüfbarer Zuordnung erstellen." />
    <div class="layout">
      <app-section-card title="1. Daten und Karte" subtitle="Die Grenzdaten werden ausschließlich vom lokalen Hub-Katalog geladen.">
        <label for="geomap-csv">CSV-Datei</label>
        <input id="geomap-csv" type="file" accept=".csv,text/csv" (change)="loadCsv($event)" />
        <label for="geomap-map">Karte</label>
        <select id="geomap-map" [(ngModel)]="mapId" (ngModelChange)="clearProjection()">
          @for (map of catalog?.maps || []; track map.id) { <option [value]="map.id">{{ map.label }}</option> }
        </select>
        <label for="geomap-region">Gebietsschlüssel</label>
        <select id="geomap-region" [(ngModel)]="regionKey">
          @for (column of columns; track column) { <option [value]="column">{{ column }}</option> }
        </select>
        <label for="geomap-value">Kennzahl</label>
        <select id="geomap-value" [(ngModel)]="valueKey">
          @for (column of columns; track column) { <option [value]="column">{{ column }}</option> }
        </select>
        <label for="geomap-aggregation">Aggregation</label>
        <select id="geomap-aggregation" [(ngModel)]="aggregation">
          @for (item of aggregations; track item) { <option [value]="item">{{ item }}</option> }
        </select>
        <label for="geomap-threshold">Mindest-Zuordnung {{ minimumMatchRatio | percent:'1.0-0' }}</label>
        <input id="geomap-threshold" type="range" min="0" max="1" step="0.05" [(ngModel)]="minimumMatchRatio" />
        <label for="geomap-source">Datenquellen-Attribution</label>
        <input id="geomap-source" [(ngModel)]="dataAttribution" maxlength="500" placeholder="z. B. Statistisches Bundesamt, 2026" />
        <div class="actions">
          <button type="button" (click)="preview()" [disabled]="busy || !ready()">Vorschau prüfen</button>
          <button type="button" class="secondary" (click)="saveDraft()" [disabled]="!mapId">Entwurf speichern</button>
          <button type="button" class="secondary" (click)="resetDraft()">Zurücksetzen</button>
        </div>
      </app-section-card>

      <app-section-card title="2. Vorschau und Veröffentlichung" subtitle="Der Hub entscheidet anhand des Join-Berichts; es gibt keine stille Zuordnung.">
        @if (projection && geometry) {
          <app-geomap-renderer [mapId]="mapId" [geometry]="geometry" [projection]="projection" />
          <dl class="quality">
            <dt>Match-Quote</dt><dd>{{ projection.report.match_ratio | percent:'1.0-1' }}</dd>
            <dt>Zugeordnet</dt><dd>{{ projection.report.matched.length }}</dd>
            <dt>Unbekannt</dt><dd>{{ projection.report.unmatched.length }}</dd>
            <dt>Duplikate</dt><dd>{{ projection.report.duplicates.length }}</dd>
          </dl>
          @if (projection.report.publication_eligible) {
            <app-explanation-notice title="Automatisch veröffentlichbar" message="Die Hub-Policy und die Mindest-Match-Quote sind erfüllt." tone="technical" />
            <div class="actions" aria-label="Kartenexport">
              @for (format of exportFormats; track format) {
                <button type="button" class="secondary" (click)="exportMap(format)" [disabled]="busy">{{ format.toUpperCase() }} exportieren</button>
              }
            </div>
          } @else {
            <app-explanation-notice title="Veröffentlichung blockiert" [message]="projection.report.reason_codes.join(', ')" tone="warning" />
          }
          @if (projection.report.unmatched.length) {
            <details><summary>Nicht zugeordnete Schlüssel</summary><p>{{ projection.report.unmatched.join(', ') }}</p></details>
          }
        } @else { <p>CSV auswählen, Spalten zuordnen und Vorschau prüfen.</p> }
      </app-section-card>
    </div>
    @if (message) { <p class="notice" aria-live="polite">{{ message }}</p> }
    @if (error) { <p class="error" role="alert">{{ error }}</p> }
  `,
  styles: [`
    .layout { display:grid; grid-template-columns:minmax(260px,1fr) minmax(440px,2fr); gap:16px; margin-top:16px; }
    label { display:block; margin-top:10px; }
    input,select { box-sizing:border-box; width:100%; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
    .secondary { opacity:.85; }
    .quality { display:grid; grid-template-columns:auto auto; max-width:320px; gap:4px 16px; }
    .quality dd { margin:0; font-weight:600; }
    .notice { padding:10px; border-left:3px solid var(--primary, #5877e8); }
    .error { padding:10px; border-left:3px solid var(--danger, #b42318); }
    @media (max-width: 900px) { .layout { grid-template-columns:1fr; } }
  `],
})
export class GeoMapStudioPageComponent implements OnInit {
  private readonly api = inject(GeoMapApiService);
  private readonly agents = inject(AgentDirectoryService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private readonly csvParser = inject(GeoMapCsvParser);
  private readonly downloads = inject(GeoMapDownloadService);
  private readonly drafts = inject(GeoMapDraftStore);

  readonly aggregations: GeoMapAggregation[] = ['preaggregated', 'sum', 'mean', 'min', 'max', 'count'];
  readonly exportFormats = ['svg', 'png', 'pdf', 'html'] as const;
  catalog: GeoMapCatalog | null = null;
  geometry: GeoMapGeometry | null = null;
  projection: GeoMapProjection | null = null;
  rows: Array<Record<string, unknown>> = [];
  columns: string[] = [];
  mapId = '';
  regionKey = '';
  valueKey = '';
  aggregation: GeoMapAggregation = 'preaggregated';
  minimumMatchRatio = 0.9;
  dataAttribution = '';
  busy = false;
  message = '';
  error = '';
  private previewRequestId = 0;

  ngOnInit(): void {
    const hub = this.hubUrl();
    if (!hub) { this.error = 'Kein Hub konfiguriert.'; return; }
    this.api.catalog(hub).subscribe({
      next: catalog => {
        this.catalog = catalog;
        const draft = this.drafts.load();
        this.mapId = draft?.mapId || catalog.maps[0]?.id || '';
        if (draft) this.applyDraft(draft);
        this.changeDetector.markForCheck();
      },
      error: error => this.fail(error),
    });
  }

  loadCsv(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.item(0);
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { this.error = 'CSV-Datei überschreitet 10 MiB.'; return; }
    file.text().then(text => {
      try {
        const parsed = this.csvParser.parse(text);
        this.rows = parsed.rows;
        this.columns = parsed.columns;
        this.regionKey = parsed.suggestedRegionKey;
        this.valueKey = parsed.suggestedValueKey;
        this.clearProjection();
        this.message = `${this.rows.length} Zeilen geladen.`;
        this.error = '';
        this.changeDetector.markForCheck();
      } catch (error) { this.fail(error); }
    }).catch(error => this.fail(error));
  }

  ready(): boolean {
    return Boolean(this.mapId && this.rows.length && this.regionKey && this.valueKey);
  }

  async preview(): Promise<void> {
    const hub = this.hubUrl();
    if (!hub || !this.ready()) return;
    const requestId = ++this.previewRequestId;
    this.busy = true;
    this.error = '';
    const mapId = this.mapId;
    try {
      const [geometry, projection] = await Promise.all([
        firstValueFrom(this.api.geometry(hub, mapId)),
        firstValueFrom(this.api.project(hub, {
          map_id: this.mapId,
          rows: this.rows,
          region_key: this.regionKey,
          value_key: this.valueKey,
          aggregation: this.aggregation,
          data_attribution: this.dataAttribution,
          minimum_match_ratio: Number(this.minimumMatchRatio),
        })),
      ]);
      if (requestId !== this.previewRequestId) return;
      this.geometry = geometry;
      this.projection = projection;
      this.busy = false;
      this.message = 'Renderer und Join-Bericht verwenden dieselbe Hub-Projektion.';
      this.changeDetector.markForCheck();
    } catch (error) {
      if (requestId === this.previewRequestId) this.fail(error);
    }
  }

  saveDraft(): void {
    this.drafts.save(this.draft());
    this.message = 'Konfiguration lokal gespeichert; eine Veröffentlichung bleibt Hub-gesteuert.';
  }

  exportMap(outputFormat: 'svg' | 'png' | 'pdf' | 'html'): void {
    const hub = this.hubUrl();
    if (!hub || !this.projection?.report.publication_eligible) return;
    this.busy = true;
    this.api.export(hub, {
      map_id: this.mapId,
      rows: this.rows,
      region_key: this.regionKey,
      value_key: this.valueKey,
      aggregation: this.aggregation,
      data_attribution: this.dataAttribution,
      minimum_match_ratio: Number(this.minimumMatchRatio),
      output_format: outputFormat,
      title: this.catalog?.maps.find(item => item.id === this.mapId)?.label || 'GeoMap',
    }).subscribe({
      next: artifact => {
        this.downloads.download(artifact);
        this.busy = false;
        this.message = `${artifact.filename} mit Registry-, Aggregations- und Attributionsmetadaten exportiert.`;
      },
      error: error => this.fail(error),
    });
  }

  resetDraft(): void {
    this.drafts.clear();
    this.rows = [];
    this.columns = [];
    this.regionKey = '';
    this.valueKey = '';
    this.aggregation = 'preaggregated';
    this.minimumMatchRatio = 0.9;
    this.dataAttribution = '';
    this.mapId = this.catalog?.maps[0]?.id || '';
    this.clearProjection();
  }

  clearProjection(): void {
    this.previewRequestId += 1;
    this.busy = false;
    this.geometry = null;
    this.projection = null;
  }

  private draft(): GeoMapDraft {
    return {
      schema: 'ananta.geomap-draft.v1',
      mapId: this.mapId,
      regionKey: this.regionKey,
      valueKey: this.valueKey,
      aggregation: this.aggregation,
      minimumMatchRatio: Number(this.minimumMatchRatio),
      dataAttribution: this.dataAttribution,
    };
  }

  private applyDraft(draft: GeoMapDraft): void {
    this.mapId = draft.mapId;
    this.regionKey = draft.regionKey;
    this.valueKey = draft.valueKey;
    this.aggregation = draft.aggregation;
    this.minimumMatchRatio = draft.minimumMatchRatio;
    this.dataAttribution = draft.dataAttribution;
  }

  private hubUrl(): string { return this.agents.list().find(agent => agent.role === 'hub')?.url || ''; }
  private fail(error: unknown): void {
    this.busy = false;
    this.error = error instanceof Error ? error.message : String(error);
    this.changeDetector.markForCheck();
  }
}
