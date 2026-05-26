import { SourcesComponent } from './sources.component.ts';

describe('SourcesComponent (source pack view)', () => {
  function createComponent(httpMock: any): SourcesComponent & { http: any } {
    const cmp = Object.create(SourcesComponent.prototype) as SourcesComponent & { http: any };
    cmp.http = httpMock;
    cmp.sources = [];
    cmp.sourcePacks = [];
    cmp.loading = false;
    cmp.error = '';
    cmp.refreshing = new Set<string>();
    cmp.bootstrapping = new Set<string>();
    cmp.citations = {};
    cmp.snapshots = {};
    cmp.packReports = {};
    return cmp;
  }

  it('loads sources and source packs', () => {
    const httpMock = {
      get: vi.fn((url: string) => ({
        subscribe: ({ next }: any) => {
          if (url === '/sources') next({ data: [{ source_id: 'keycloak-official-docs' }] });
          if (url === '/sources/packs') next({ data: [{ source_pack_id: 'ananta-dev-default', display_name: 'Default', version: '1', sources: [] }] });
        },
      })),
    };
    const cmp = createComponent(httpMock);
    cmp.loadSources();
    expect(cmp.sources.length).toBe(1);
    expect(cmp.sourcePacks[0].source_pack_id).toBe('ananta-dev-default');
  });

  it('bootstraps source pack and stores report', () => {
    const httpMock = {
      post: vi.fn((_url: string, _body: any) => ({
        subscribe: ({ next }: any) => next({ data: { status: 'planned' } }),
      })),
      get: vi.fn((_url: string) => ({
        subscribe: ({ next }: any) => next({ data: [] }),
      })),
    };
    const cmp = createComponent(httpMock);
    cmp.bootstrapPack('ananta-dev-default', true);
    expect(cmp.packReports['ananta-dev-default'].status).toBe('planned');
  });
});
