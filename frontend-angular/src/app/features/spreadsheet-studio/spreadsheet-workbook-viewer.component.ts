import { CommonModule } from '@angular/common';
import { DestroyRef, Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, catchError, map, of, switchMap } from 'rxjs';

import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import {
  SpreadsheetCell,
  SpreadsheetDocument,
  SpreadsheetRangeSelection,
  SpreadsheetViewport,
} from './spreadsheet-studio.models';

interface ViewportRequest {
  document: SpreadsheetDocument;
  sheetId: string;
  start: string;
  end: string;
  offset: number;
  append: boolean;
}

@Component({
  selector: 'app-spreadsheet-workbook-viewer',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="viewer" aria-labelledby="workbook-viewer-title">
      <header>
        <div>
          <strong id="workbook-viewer-title">Versionsgebundene Workbook-Projektion</strong>
          <p>Interaktionen verwenden ausschließlich paginierte Hub-Zellen; die vollständige Originaldatei bleibt erhalten.</p>
        </div>
        <label>Version
          <select [ngModel]="activeDocument?.version" (ngModelChange)="selectVersion($event)" [disabled]="loadingVersion">
            @for (version of versions; track version.version) {
              <option [ngValue]="version.version">v{{ version.version }}</option>
            }
          </select>
        </label>
        <label>Zoom
          <select [(ngModel)]="zoom" aria-label="Zoomstufe">
            <option [ngValue]="75">75 %</option><option [ngValue]="100">100 %</option>
            <option [ngValue]="125">125 %</option><option [ngValue]="150">150 %</option>
          </select>
        </label>
      </header>

      @if (activeDocument) {
        <div class="binding" aria-live="polite">
          <span>{{ activeDocument.title }} · v{{ activeDocument.version }}</span>
          <code>{{ activeDocument.snapshot_digest }}</code>
          <span [class.sync-ok]="synchronized" [class.sync-error]="!synchronized">
            {{ synchronized ? 'Viewport und Version synchron' : 'Synchronisierung ausstehend oder fehlgeschlagen' }}
          </span>
        </div>
        <div class="sheet-tabs" role="tablist" aria-label="Arbeitsblätter">
          @for (sheet of activeDocument.snapshot.sheets; track sheet.sheet_id) {
            <button type="button" role="tab"
              [attr.aria-selected]="activeSheetId === sheet.sheet_id"
              [class.active]="activeSheetId === sheet.sheet_id"
              (click)="selectSheet(sheet.sheet_id)">
              {{ sheet.name }}{{ sheet.hidden ? ' (verborgen)' : '' }}
            </button>
          }
        </div>
        <div class="range-controls">
          <label>Start <input [(ngModel)]="rangeStart" maxlength="10" aria-label="Startzelle" /></label>
          <label>Ende <input [(ngModel)]="rangeEnd" maxlength="10" aria-label="Endzelle" /></label>
          <button type="button" (click)="loadRange()" [disabled]="loading || !validRange()">Bereich laden</button>
          <span>max. 10.000 Zellen je Viewport · 250 belegte Zellen je Seite · max. 24 DOM-Zeilen</span>
        </div>

        @if (unsupportedObjects().length) {
          <div class="warning" role="status">
            Nicht unterstützte Objekte: {{ unsupportedObjects().join(', ') }}. Änderungen bleiben für diese Objekte gesperrt.
          </div>
        }
        @if (error) { <p class="error" role="alert">{{ error }}</p> }
        @if (viewport) {
          <div class="grid-meta">
            <span>{{ viewport.range.start }}:{{ viewport.range.end }}</span>
            <span>{{ cells.length }} / {{ viewport.total }} belegte Zellen geladen</span>
            <span>{{ viewport.backend_cell_count }} Zellen im vollständigen Backend-Artefakt</span>
          </div>
          <div class="virtual-grid" role="grid" tabindex="0"
            [attr.aria-rowcount]="viewport.total"
            [attr.aria-busy]="loading"
            [style.--viewer-zoom]="zoom / 100"
            (scroll)="onScroll($event)">
            <div [style.height.px]="topSpacer()" aria-hidden="true"></div>
            @for (cell of visibleCells(); track cell.address) {
              <button type="button" class="cell-row" role="row" (click)="selectCell(cell)"
                [attr.aria-label]="cellLabel(cell)">
                <span role="gridcell" class="address">{{ cell.address }}</span>
                <span role="gridcell" class="value">{{ displayValue(cell) }}</span>
                <span role="gridcell" class="formula">{{ formulaValue(cell) }}</span>
              </button>
            }
            <div [style.height.px]="bottomSpacer()" aria-hidden="true"></div>
          </div>
          @if (loading) { <p aria-live="polite">Viewport wird geladen …</p> }
        }
      }
    </section>
  `,
  styles: [`
    .viewer { display:grid; gap:10px; min-width:0; }
    header,.range-controls,.binding,.grid-meta,.sheet-tabs { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    header { justify-content:space-between; }
    header p { margin:3px 0 0; max-width:680px; }
    label { display:grid; gap:3px; }
    .binding { padding:8px; background:var(--surface-muted, #eef1f8); }
    .binding code { max-width:420px; overflow:hidden; text-overflow:ellipsis; }
    .sync-ok { color:var(--success, #18794e); }
    .sync-error,.error { color:var(--danger, #b42318); }
    .sheet-tabs { border-bottom:1px solid var(--border-color, #778); }
    .sheet-tabs button.active { border-bottom:3px solid var(--primary, #5877e8); }
    .range-controls span { font-size:.85rem; opacity:.8; }
    .warning { padding:8px; border-left:4px solid var(--warning, #b7791f); }
    .grid-meta { justify-content:space-between; font-size:.9rem; }
    .virtual-grid { height:320px; overflow:auto; border:1px solid var(--border-color, #778); contain:strict; }
    .cell-row { width:100%; min-height:calc(36px * var(--viewer-zoom)); display:grid;
      grid-template-columns:minmax(72px, .35fr) minmax(120px, 1fr) minmax(160px, 1.4fr);
      align-items:center; gap:8px; text-align:left; border:0; border-bottom:1px solid var(--border-color, #dde);
      background:var(--surface, #fff); font-size:calc(.9rem * var(--viewer-zoom)); }
    .cell-row:focus-visible { outline:3px solid var(--primary, #5877e8); outline-offset:-3px; }
    .address { font-family:monospace; font-weight:700; }
    .value,.formula { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    @media(max-width:700px){.formula{display:none}.cell-row{grid-template-columns:80px 1fr}}
  `],
})
export class SpreadsheetWorkbookViewerComponent implements OnChanges {
  private static readonly PAGE_SIZE = 250;
  private static readonly MAX_DOM_ROWS = 24;
  private static readonly ROW_HEIGHT = 36;
  private readonly api = inject(SpreadsheetStudioApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly requests = new Subject<ViewportRequest>();

  @Input({ required: true }) hubUrl = '';
  @Input({ required: true }) document: SpreadsheetDocument | null = null;
  @Output() documentVersionChange = new EventEmitter<SpreadsheetDocument>();
  @Output() selectionChange = new EventEmitter<SpreadsheetRangeSelection>();
  @Output() synchronizationChange = new EventEmitter<boolean>();

  versions: SpreadsheetDocument[] = [];
  activeDocument: SpreadsheetDocument | null = null;
  activeSheetId = '';
  rangeStart = 'A1';
  rangeEnd = 'Z100';
  zoom = 100;
  viewport: SpreadsheetViewport | null = null;
  cells: SpreadsheetCell[] = [];
  windowStart = 0;
  loading = false;
  loadingVersion = false;
  synchronized = false;
  error = '';

  constructor() {
    this.requests.pipe(
      switchMap(request => this.api.viewport(
        this.hubUrl,
        request.document.document_id,
        request.document.version,
        {
          sheetId: request.sheetId,
          start: request.start,
          end: request.end,
          offset: request.offset,
          limit: SpreadsheetWorkbookViewerComponent.PAGE_SIZE,
        },
      ).pipe(
        map(page => ({ request, page, error: '' })),
        catchError(error => of({ request, page: null, error: this.errorMessage(error) })),
      )),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(result => this.acceptViewport(result.request, result.page, result.error));
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['document'] && !changes['hubUrl']) return;
    this.activeDocument = this.document;
    this.versions = this.document ? [this.document] : [];
    this.activeSheetId = this.defaultSheetId(this.document);
    this.viewport = null;
    this.cells = [];
    this.setSynchronized(false);
    if (!this.hubUrl || !this.document) return;
    const requestedDocumentId = this.document.document_id;
    this.api.listVersions(this.hubUrl, requestedDocumentId).pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: page => {
        if (this.document?.document_id !== requestedDocumentId) return;
        this.versions = page.items;
      },
      error: error => this.error = this.errorMessage(error),
    });
    this.loadRange();
  }

  selectVersion(version: number): void {
    if (!this.document || !Number.isInteger(Number(version))) return;
    const cached = this.versions.find(item => item.version === Number(version));
    if (cached) {
      this.activateVersion(cached);
      return;
    }
    this.loadingVersion = true;
    this.api.getVersion(this.hubUrl, this.document.document_id, Number(version)).pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: value => { this.loadingVersion = false; this.activateVersion(value); },
      error: error => { this.loadingVersion = false; this.error = this.errorMessage(error); },
    });
  }

  selectSheet(sheetId: string): void {
    if (!this.activeDocument?.snapshot.sheets.some(sheet => sheet.sheet_id === sheetId)) return;
    this.activeSheetId = sheetId;
    this.loadRange();
  }

  loadRange(): void {
    if (!this.activeDocument || !this.activeSheetId || !this.validRange()) return;
    this.cells = [];
    this.viewport = null;
    this.windowStart = 0;
    this.requestPage(0, false);
    this.emitSelection();
  }

  validRange(): boolean {
    return this.validCell(this.rangeStart) && this.validCell(this.rangeEnd);
  }

  onScroll(event: Event): void {
    const element = event.currentTarget as HTMLElement;
    const rowHeight = SpreadsheetWorkbookViewerComponent.ROW_HEIGHT * (this.zoom / 100);
    this.windowStart = Math.max(0, Math.floor(element.scrollTop / rowHeight) - 4);
    if (!this.loading && this.viewport?.has_more && this.windowStart + 40 >= this.cells.length) {
      this.requestPage(this.cells.length, true);
    }
  }

  visibleCells(): SpreadsheetCell[] {
    return this.cells.slice(
      this.windowStart,
      this.windowStart + SpreadsheetWorkbookViewerComponent.MAX_DOM_ROWS,
    );
  }

  topSpacer(): number { return this.windowStart * SpreadsheetWorkbookViewerComponent.ROW_HEIGHT * (this.zoom / 100); }

  bottomSpacer(): number {
    const remaining = Math.max(0, this.cells.length - this.windowStart - this.visibleCells().length);
    return remaining * SpreadsheetWorkbookViewerComponent.ROW_HEIGHT * (this.zoom / 100);
  }

  selectCell(cell: SpreadsheetCell): void {
    this.rangeStart = cell.address;
    this.rangeEnd = cell.address;
    this.emitSelection();
  }

  displayValue(cell: SpreadsheetCell): string {
    return String(cell.displayed_value ?? cell.value ?? '');
  }

  formulaValue(cell: SpreadsheetCell): string {
    return String(cell.formula_text || (cell.formula ? JSON.stringify(cell.formula) : ''));
  }

  cellLabel(cell: SpreadsheetCell): string {
    const formula = this.formulaValue(cell);
    return `${cell.address}, Wert ${this.displayValue(cell)}${formula ? `, Formel ${formula}` : ''}`;
  }

  unsupportedObjects(): string[] { return this.activeDocument?.unsupported_objects || []; }

  private activateVersion(document: SpreadsheetDocument): void {
    this.activeDocument = document;
    this.activeSheetId = this.defaultSheetId(document);
    this.documentVersionChange.emit(document);
    this.loadRange();
  }

  private requestPage(offset: number, append: boolean): void {
    if (!this.activeDocument) return;
    this.loading = true;
    this.error = '';
    this.setSynchronized(false);
    this.requests.next({
      document: this.activeDocument,
      sheetId: this.activeSheetId,
      start: this.rangeStart.trim().toUpperCase(),
      end: this.rangeEnd.trim().toUpperCase(),
      offset,
      append,
    });
  }

  private acceptViewport(request: ViewportRequest, page: SpreadsheetViewport | null, error: string): void {
    if (request.document.document_id !== this.activeDocument?.document_id || request.document.version !== this.activeDocument.version) return;
    this.loading = false;
    if (!page) { this.error = error; this.setSynchronized(false); return; }
    if (page.snapshot_digest !== request.document.snapshot_digest || page.sheet_id !== request.sheetId) {
      this.error = 'Viewport, Snapshot und Dokumentversion sind nicht synchron. Änderungen bleiben gesperrt.';
      this.cells = [];
      this.viewport = page;
      this.setSynchronized(false);
      return;
    }
    this.viewport = page;
    this.cells = request.append ? [...this.cells, ...page.cells] : page.cells;
    this.setSynchronized(true);
  }

  private emitSelection(): void {
    if (!this.activeDocument || !this.activeSheetId || !this.validRange()) return;
    this.selectionChange.emit({
      document_id: this.activeDocument.document_id,
      document_version: this.activeDocument.version,
      snapshot_digest: this.activeDocument.snapshot_digest,
      sheet_id: this.activeSheetId,
      start: this.rangeStart.trim().toUpperCase(),
      end: this.rangeEnd.trim().toUpperCase(),
    });
  }

  private setSynchronized(value: boolean): void {
    if (this.synchronized === value) return;
    this.synchronized = value;
    this.synchronizationChange.emit(value);
  }

  private defaultSheetId(document: SpreadsheetDocument | null): string {
    const sheets = document?.snapshot.sheets || [];
    return (sheets.find(sheet => !sheet.hidden) || sheets[0])?.sheet_id || '';
  }

  private validCell(value: string): boolean { return /^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(value.trim().toUpperCase()); }

  private errorMessage(error: unknown): string {
    const response = error as { error?: { message?: string }; message?: string };
    return response.error?.message || response.message || 'Viewport konnte nicht geladen werden.';
  }
}
