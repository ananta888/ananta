import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';

import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import { SpreadsheetDocument, SpreadsheetViewport } from './spreadsheet-studio.models';
import { SpreadsheetWorkbookViewerComponent } from './spreadsheet-workbook-viewer.component';

function workbook(): SpreadsheetDocument {
  return {
    schema: 'ananta.spreadsheet-document-version.v1',
    document_id: 'document-one',
    title: 'Workbook',
    version: 2,
    snapshot: {
      schema: 'ananta.spreadsheet-workbook-snapshot.v1',
      snapshot_id: 'snapshot-two',
      document_version_id: 'version-two',
      sheets: [{ sheet_id: 'sheet-one', name: 'Sheet 1', hidden: false, cells: [] }],
    },
    snapshot_digest: 'a'.repeat(64),
    state: 'published',
    unsupported_objects: [],
    source_grounding_verified: false,
    human_intervention_required: false,
  };
}

function viewport(digest: string, count = 100): SpreadsheetViewport {
  return {
    schema: 'ananta.spreadsheet-workbook-viewport.v1',
    snapshot_digest: digest,
    sheet_id: 'sheet-one',
    range: { start: 'A1', end: 'Z100' },
    tile: { row: 1, column: 1, rows: 100, columns: 26 },
    offset: 0,
    limit: 250,
    total: count,
    has_more: false,
    cells: Array.from({ length: count }, (_, index) => ({
      address: `A${index + 1}`,
      value: index,
      formula: null,
      style_ref: null,
    })),
    projection_digest: 'b'.repeat(64),
    backend_cell_count: 5_000,
    source_grounding_verified: false,
    human_intervention_required: false,
  };
}

describe('SpreadsheetWorkbookViewerComponent', () => {
  let fixture: ComponentFixture<SpreadsheetWorkbookViewerComponent>;
  let pages: Subject<SpreadsheetViewport>[];
  const api = {
    listVersions: vi.fn(),
    getVersion: vi.fn(),
    viewport: vi.fn(),
  };

  beforeEach(() => {
    pages = [];
    vi.clearAllMocks();
    api.listVersions.mockReturnValue(of({ items: [workbook()], limit: 100 }));
    api.viewport.mockImplementation(() => {
      const page = new Subject<SpreadsheetViewport>();
      pages.push(page);
      return page;
    });
    TestBed.configureTestingModule({
      imports: [SpreadsheetWorkbookViewerComponent],
      providers: [{ provide: SpreadsheetStudioApiService, useValue: api }],
    });
    fixture = TestBed.createComponent(SpreadsheetWorkbookViewerComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('document', workbook());
    fixture.detectChanges();
  });

  it('cancels stale viewport requests and blocks a mismatching version digest', () => {
    expect(api.viewport).toHaveBeenCalledWith(
      'http://hub.test', 'document-one', 2,
      expect.objectContaining({ sheetId: 'sheet-one', offset: 0, limit: 250 }),
    );
    expect(pages[0].observed).toBe(true);

    fixture.componentInstance.rangeEnd = 'B20';
    fixture.componentInstance.loadRange();
    expect(pages[0].observed).toBe(false);
    pages[1].next(viewport('c'.repeat(64)));
    fixture.detectChanges();

    expect(fixture.componentInstance.synchronized).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('Änderungen bleiben gesperrt');
  });

  it('renders at most 24 accessible DOM rows while preserving backend totals', () => {
    pages[0].next(viewport('a'.repeat(64)));
    fixture.detectChanges();

    expect(fixture.componentInstance.synchronized).toBe(true);
    expect(fixture.nativeElement.querySelectorAll('.cell-row')).toHaveLength(24);
    expect(fixture.nativeElement.textContent).toContain('5000 Zellen im vollständigen Backend-Artefakt');
    expect(fixture.nativeElement.querySelector('[role="grid"]')?.getAttribute('aria-rowcount')).toBe('100');
  });
});
