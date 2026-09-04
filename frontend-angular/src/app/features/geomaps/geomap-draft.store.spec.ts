import { GeoMapDraftStore } from './geomap-draft.store';

describe('GeoMapDraftStore', () => {
  beforeEach(() => localStorage.clear());

  it('round-trips a versioned configuration and rejects corrupt state', () => {
    const store = new GeoMapDraftStore();
    const draft = {
      schema: 'ananta.geomap-draft.v1' as const,
      mapId: 'de-states', regionKey: 'region', valueKey: 'value',
      aggregation: 'sum' as const, minimumMatchRatio: 0.95, dataAttribution: 'Fixture',
    };
    store.save(draft);
    expect(store.load()).toEqual(draft);
    localStorage.setItem('ananta.geomap.draft.v1', '{broken');
    expect(store.load()).toBeNull();
  });
});
