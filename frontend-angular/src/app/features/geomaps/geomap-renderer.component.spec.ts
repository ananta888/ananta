import { TestBed } from '@angular/core/testing';

const echartsMock = vi.hoisted(() => {
  const chart = {
    dispatchAction: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn(),
    resize: vi.fn(),
    setOption: vi.fn(),
  };
  return {
    chart,
    init: vi.fn(() => chart),
    registerMap: vi.fn(),
    use: vi.fn(),
  };
});

vi.mock('echarts/core', () => echartsMock);

import { GeoMapRendererComponent } from './geomap-renderer.component';

describe('GeoMapRendererComponent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({ imports: [GeoMapRendererComponent] });
  });

  it('renders registry geometry with neutral missing-data styling and an informative tooltip', () => {
    const fixture = TestBed.createComponent(GeoMapRendererComponent);
    fixture.componentRef.setInput('mapId', 'de-states');
    fixture.componentRef.setInput('geometry', {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        properties: { id: 'DE-BE', name: 'Berlin' },
        geometry: { type: 'Polygon', coordinates: [] },
      }],
    });
    fixture.componentRef.setInput('projection', {
      schema: 'ananta.geomap-projection.v1',
      map_id: 'de-states',
      registry_version: 1,
      aggregation: 'sum',
      values: [{ region_id: 'DE-BE', name: 'Berlin', value: 2, source_rows: 1 }],
      report: {
        matched: ['DE-BE'], unmatched: [], duplicates: [], missing_geometry: ['DE-BB'],
        invalid_values: [], match_ratio: 1, minimum_match_ratio: 0.9,
        publication_eligible: true, reason_codes: [],
      },
      map_attribution: 'Map source',
      data_attribution: 'Data source',
    });

    fixture.detectChanges();

    expect(echartsMock.registerMap).toHaveBeenCalledWith('de-states', expect.any(Object));
    const option = echartsMock.chart.setOption.mock.calls.at(-1)?.[0];
    expect(option.series[0].itemStyle.areaColor).toBe('#d9dee8');
    expect(option.tooltip.formatter({
      data: { name: 'Berlin', value: 2, sourceRows: 1 },
    })).toContain('Status: zugeordnet');
  });

  it('supports deterministic keyboard selection and zoom reset actions', () => {
    const fixture = TestBed.createComponent(GeoMapRendererComponent);
    fixture.detectChanges();

    fixture.componentInstance.select('Berlin');
    fixture.componentInstance.reset();

    expect(echartsMock.chart.dispatchAction).toHaveBeenNthCalledWith(1, {
      type: 'mapSelect', name: 'Berlin',
    });
    expect(echartsMock.chart.dispatchAction).toHaveBeenNthCalledWith(2, { type: 'restore' });
    expect(fixture.componentInstance.selectedName).toBe('');
  });
});
