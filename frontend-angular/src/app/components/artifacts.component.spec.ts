import { of } from 'rxjs';

import { ArtifactsComponent } from './artifacts.component';

describe('ArtifactsComponent', () => {
  const hubApiMock = {
    listArtifacts: vi.fn(() => of([])),
    getArtifact: vi.fn(() => of({})),
    uploadArtifact: vi.fn(() => of({ artifact: { id: 'artifact-1' } })),
    extractArtifact: vi.fn(() => of({})),
  };

  function createComponent(): ArtifactsComponent {
    const cmp = Object.create(ArtifactsComponent.prototype) as ArtifactsComponent & { hubApi: any; ns: any; hub: any };
    cmp.hubApi = hubApiMock;
    cmp.ns = { success: vi.fn(), error: vi.fn(), fromApiError: vi.fn((_e: any, fallback: string) => fallback) };
    cmp.hub = { url: 'http://hub:5000', role: 'hub' };
    cmp.artifacts = [];
    cmp.selectedArtifactId = null;
    cmp.selectedArtifact = null;
    cmp.collectionName = '';
    cmp.selectedFile = null;
    cmp.loadingList = false;
    cmp.loadingDetail = false;
    cmp.uploadBusy = false;
    cmp.extractBusy = false;
    return cmp;
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('extracts unique knowledge collection names from knowledge links', () => {
    const cmp = createComponent();
    const names = cmp.knowledgeCollectionNames({
      knowledge_links: [
        { collection_id: 'col-1', link_metadata: { collection_name: 'team-docs' } },
        { collection_id: 'col-2', link_metadata: { collection_name: 'team-docs' } },
        { collection_id: 'col-3', link_metadata: { collection_name: 'research' } },
      ],
    });

    expect(names).toEqual(['team-docs', 'research']);
  });

  it('uploads selected file via hub api and refreshes selection', () => {
    const cmp = createComponent();
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' });
    cmp.selectedFile = file;
    cmp.collectionName = 'team-docs';
    cmp.refresh = vi.fn();
    cmp.selectArtifact = vi.fn();

    cmp.upload();

    expect(hubApiMock.uploadArtifact).toHaveBeenCalledWith('http://hub:5000', file, 'team-docs');
    expect(cmp.ns.success).toHaveBeenCalledWith('Artefakt hochgeladen');
    expect(cmp.refresh).toHaveBeenCalled();
    expect(cmp.selectArtifact).toHaveBeenCalledWith('artifact-1');
  });
});
