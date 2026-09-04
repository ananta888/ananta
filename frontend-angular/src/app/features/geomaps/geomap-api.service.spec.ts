import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { GeoMapApiService } from './geomap-api.service';

describe('GeoMapApiService', () => {
  const core = { get: vi.fn(), post: vi.fn() };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [GeoMapApiService, { provide: HubApiCoreService, useValue: core }],
    });
  });

  it('uses only authenticated Hub catalog and geometry endpoints', async () => {
    core.get.mockReturnValue(of({}));
    const api = TestBed.inject(GeoMapApiService);
    await firstValueFrom(api.catalog('http://hub.test/'));
    await firstValueFrom(api.geometry('http://hub.test', 'de/states'));
    expect(core.get).toHaveBeenNthCalledWith(1, 'http://hub.test/api/geomaps/registry', 'http://hub.test/');
    expect(core.get).toHaveBeenNthCalledWith(
      2,
      'http://hub.test/api/geomaps/de%2Fstates/geometry',
      'http://hub.test',
      undefined,
      false,
      60_000,
    );
  });

  it('sends the complete deterministic join command to the Hub', async () => {
    core.post.mockReturnValue(of({ schema: 'ananta.geomap-projection.v1' }));
    const api = TestBed.inject(GeoMapApiService);
    const command = {
      map_id: 'de-states', rows: [{ region: 'DE-BE', value: 1 }],
      region_key: 'region', value_key: 'value', aggregation: 'sum' as const,
      data_attribution: 'Fixture', minimum_match_ratio: 1,
    };
    await firstValueFrom(api.project('http://hub.test', command));
    expect(core.post).toHaveBeenCalledWith('http://hub.test/api/geomaps/project', command, 'http://hub.test');
  });

  it('requests a headless export from the Hub instead of rendering policy in the client', async () => {
    core.post.mockReturnValue(of({ schema: 'ananta.geomap-export-artifact.v1' }));
    const api = TestBed.inject(GeoMapApiService);
    const command = {
      map_id: 'de-states', rows: [{ region: 'DE-BE', value: 1 }],
      region_key: 'region', value_key: 'value', aggregation: 'sum' as const,
      data_attribution: 'Fixture', minimum_match_ratio: 1,
      output_format: 'pdf' as const, title: 'Map',
    };
    await firstValueFrom(api.export('http://hub.test', command));
    expect(core.post).toHaveBeenCalledWith('http://hub.test/api/geomaps/export', command, 'http://hub.test');
  });
});
