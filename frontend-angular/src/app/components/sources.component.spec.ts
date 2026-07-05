import { of, throwError } from 'rxjs';
import { SourcesComponent } from './sources.component';

describe('SourcesComponent', () => {
  function api(overrides: Record<string, any> = {}): any {
    return {
      listSources: vi.fn(() => of([])),
      listPacks: vi.fn(() => of([])),
      bootstrapPack: vi.fn(() => of({ status: 'planned' })),
      refresh: vi.fn(() => of({})),
      citation: vi.fn(() => of({ human_readable: 'Citation' })),
      snapshots: vi.fn(() => of([])),
      ...overrides,
    };
  }

  it('renders empty and populated source states through its view model', () => {
    const empty = new SourcesComponent(api());
    empty.loadSources();
    expect(empty.sources).toEqual([]);

    const populated = new SourcesComponent(api({
      listSources: () => of([{
        source_id: 'open-notebook-1',
        source_type: 'open_notebook',
        display_name: 'Notebook',
        trust_level: 'user_managed_research',
      }]),
    }));
    populated.loadSources();
    expect(populated.sources[0].source_type).toBe('open_notebook');
  });

  it('surfaces service failures', () => {
    const component = new SourcesComponent(api({ listSources: () => throwError(() => new Error('failed')) }));
    component.loadSources();
    expect(component.error).toContain('failed');
  });

  it('loads snapshots and citation details', () => {
    const component = new SourcesComponent(api({
      snapshots: () => of([{ snapshot_id: 'snap_1', status: 'indexed' }]),
    }));
    const source: any = { source_id: 'open-notebook-1', source_type: 'open_notebook' };
    component.showDetails(source);
    component.loadCitation(source.source_id);
    expect(component.snapshots[source.source_id][0].status).toBe('indexed');
    expect(component.citations[source.source_id]).toBe('Citation');
  });
});
