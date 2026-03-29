import { of } from 'rxjs';

import { ArtifactsComponent } from './artifacts.component';

describe('ArtifactsComponent', () => {
  const hubApiMock = {
    listArtifacts: vi.fn(() => of([])),
    getArtifact: vi.fn(() => of({})),
    uploadArtifact: vi.fn(() => of({ artifact: { id: 'artifact-1' } })),
    extractArtifact: vi.fn(() => of({})),
    indexArtifact: vi.fn(() => of({})),
    getArtifactRagStatus: vi.fn(() => of({ knowledge_index: { status: 'completed' } })),
    getArtifactRagPreview: vi.fn(() => of({ manifest: { file_count: 1 }, preview: { index: [] } })),
    listKnowledgeCollections: vi.fn(() => of([])),
    createKnowledgeCollection: vi.fn(() => of({ id: 'collection-1' })),
    getKnowledgeCollection: vi.fn(() => of({ collection: { id: 'collection-1' }, knowledge_links: [], knowledge_indices: [] })),
    indexKnowledgeCollection: vi.fn(() => of({})),
    searchKnowledgeCollection: vi.fn(() => of({ chunks: [{ source: 'README.md', content: 'timeout handling' }] })),
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
    cmp.loadingCollections = false;
    cmp.uploadBusy = false;
    cmp.extractBusy = false;
    cmp.indexBusy = false;
    cmp.previewBusy = false;
    cmp.collectionBusy = false;
    cmp.collectionIndexBusy = false;
    cmp.searchBusy = false;
    cmp.knowledgeCollections = [];
    cmp.selectedCollectionId = null;
    cmp.selectedCollectionDetail = null;
    cmp.artifactRagStatus = null;
    cmp.artifactRagPreview = null;
    cmp.knowledgeSearchQuery = '';
    cmp.knowledgeSearchResults = [];
    cmp.newCollectionName = '';
    cmp.newCollectionDescription = '';
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

  it('creates a knowledge collection and refreshes selection', () => {
    const cmp = createComponent();
    cmp.newCollectionName = 'payments-docs';
    cmp.newCollectionDescription = 'payment flows';
    cmp.loadCollections = vi.fn();
    cmp.selectCollection = vi.fn();

    ArtifactsComponent.prototype.createCollection.call(cmp);

    expect(hubApiMock.createKnowledgeCollection).toHaveBeenCalledWith('http://hub:5000', {
      name: 'payments-docs',
      description: 'payment flows',
    });
    expect(cmp.ns.success).toHaveBeenCalledWith('Collection angelegt');
    expect(cmp.loadCollections).toHaveBeenCalled();
    expect(cmp.selectCollection).toHaveBeenCalledWith('collection-1');
  });

  it('searches the selected collection and stores chunks', () => {
    const cmp = createComponent();
    cmp.selectedCollectionId = 'collection-1';
    cmp.knowledgeSearchQuery = 'timeout';

    ArtifactsComponent.prototype.searchSelectedCollection.call(cmp);

    expect(hubApiMock.searchKnowledgeCollection).toHaveBeenCalledWith('http://hub:5000', 'collection-1', {
      query: 'timeout',
      top_k: 5,
    });
    expect(cmp.knowledgeSearchResults).toEqual([{ source: 'README.md', content: 'timeout handling' }]);
  });
});
