import { TestBed } from '@angular/core/testing';
import { ChangeDetectorRef } from '@angular/core';
import { Subject, of } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { GeoMapApiService } from './geomap-api.service';
import { GeoMapCsvParser } from './geomap-csv-parser.service';
import { GeoMapDownloadService } from './geomap-download.service';
import { GeoMapDraftStore } from './geomap-draft.store';
import { GeoMapStudioPageComponent } from './geomap-studio-page.component';

describe('GeoMapStudioPageComponent', () => {
  function component(api: Record<string, ReturnType<typeof vi.fn>>, markForCheck = vi.fn()) {
    TestBed.configureTestingModule({
      providers: [
        { provide: GeoMapApiService, useValue: api },
        { provide: GeoMapCsvParser, useValue: { parse: vi.fn() } },
        { provide: GeoMapDownloadService, useValue: { download: vi.fn() } },
        { provide: GeoMapDraftStore, useValue: { load: () => null, save: vi.fn(), clear: vi.fn() } },
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] } },
        { provide: ChangeDetectorRef, useValue: { markForCheck } },
      ],
    });
    return TestBed.runInInjectionContext(() => new GeoMapStudioPageComponent());
  }

  it('marks the zoneless view when the async map catalog arrives', () => {
    const catalog = new Subject<never>();
    const markForCheck = vi.fn();
    const page = component({ catalog: vi.fn().mockReturnValue(catalog) }, markForCheck);

    page.ngOnInit();
    catalog.next({
      schema: 'ananta.geomap-registry.v1',
      version: 1,
      maps: [{ id: 'de-states', label: 'States' }],
    } as never);

    expect(page.mapId).toBe('de-states');
    expect(markForCheck).toHaveBeenCalledOnce();
  });

  it('loads geometry and projection together and honors the Hub publication decision', async () => {
    const projection = {
      schema: 'ananta.geomap-projection.v1', map_id: 'de-states', registry_version: 1,
      aggregation: 'sum', values: [], map_attribution: 'Map', data_attribution: 'Data',
      report: {
        matched: [], unmatched: ['XX'], duplicates: [], missing_geometry: [], invalid_values: [],
        match_ratio: 0, minimum_match_ratio: 0.9, publication_eligible: false,
        reason_codes: ['geomap_match_ratio_below_threshold'],
      },
    };
    const api = {
      geometry: vi.fn().mockReturnValue(of({ type: 'FeatureCollection', features: [] })),
      project: vi.fn().mockReturnValue(of(projection)),
    };
    const page = component(api);
    page.mapId = 'de-states';
    page.rows = [{ region: 'XX', value: 1 }];
    page.regionKey = 'region';
    page.valueKey = 'value';
    await page.preview();
    expect(api.project).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({ minimum_match_ratio: 0.9 }));
    expect(page.projection?.report.publication_eligible).toBe(false);
    expect(page.busy).toBe(false);
  });

  it('does not require transport observables to complete before rendering the projection', async () => {
    const geometry = new Subject<never>();
    const projection = new Subject<never>();
    const page = component({
      geometry: vi.fn().mockReturnValue(geometry),
      project: vi.fn().mockReturnValue(projection),
    });
    page.mapId = 'de-states';
    page.rows = [{ region: 'DE-BE', value: 1 }];
    page.regionKey = 'region';
    page.valueKey = 'value';
    const completed = page.preview();
    geometry.next({ type: 'FeatureCollection', features: [] } as never);
    projection.next({
      schema: 'ananta.geomap-projection.v1', map_id: 'de-states', registry_version: 1,
      aggregation: 'sum', values: [], map_attribution: 'Map', data_attribution: 'Data',
      report: {
        matched: [], unmatched: [], duplicates: [], missing_geometry: [], invalid_values: [],
        match_ratio: 1, minimum_match_ratio: 0.9, publication_eligible: true, reason_codes: [],
      },
    } as never);
    await completed;
    expect(page.projection?.report.publication_eligible).toBe(true);
    expect(page.busy).toBe(false);
  });
});
