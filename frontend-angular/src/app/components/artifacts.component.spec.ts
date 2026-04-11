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
    getTaskOrchestrationReadModel: vi.fn(() => of({ artifact_flow: { items: [], groups: { by_worker: [], by_assignment: [] } } })),
  };
  const agentApiMock = {
    taskWorkspaceFiles: vi.fn(() => of({ workspace: { files: [] } })),
  };

  function createComponent(): ArtifactsComponent {
    const cmp = Object.create(ArtifactsComponent.prototype) as ArtifactsComponent & { hubApi: any; ns: any; hub: any; agentApi: any };
    cmp.hubApi = hubApiMock;
    cmp.agentApi = agentApiMock;
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
    cmp.selectedArtifactProfileName = 'default';
    cmp.selectedCollectionProfileName = 'default';
    cmp.artifactFlowReadModel = null;
    cmp.loadingArtifactFlow = false;
    cmp.selectedWorkspaceRunKey = '';
    cmp.workspaceTrackedOnly = true;
    cmp.workspaceLoading = false;
    cmp.workspaceLoadError = '';
    cmp.workspaceFilePayload = null;
    cmp.workspaceTreeLineItems = [];
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

  it('loads execution artifact flow data from the orchestration read model', () => {
    const cmp = createComponent();
    hubApiMock.getTaskOrchestrationReadModel.mockReturnValueOnce(of({
      artifact_flow: {
        items: [{ item_id: 'task-1', sent_artifacts: [{ artifact_id: 'artifact-1', label: 'diff.patch' }] }],
        groups: {
          by_worker: [{ worker_url: 'http://alpha:5001', artifacts: [{ artifact_id: 'artifact-1', label: 'diff.patch' }] }],
          by_assignment: [{ assignment_key: 'alpha::tmpl', template_name: 'Python Worker Template', artifacts: [{ artifact_id: 'artifact-1', label: 'diff.patch' }] }],
        },
      },
    }));

    cmp.loadArtifactFlow();

    expect(hubApiMock.getTaskOrchestrationReadModel).toHaveBeenCalledWith('http://hub:5000');
    expect(cmp.artifactFlowItems()).toHaveLength(1);
    expect(cmp.artifactFlowWorkerGroups()).toHaveLength(1);
    expect(cmp.artifactFlowAssignmentGroups()).toHaveLength(1);
  });

  it('deduplicates artifact summaries and opens artifact details from explorer entries', () => {
    const cmp = createComponent();
    cmp.selectArtifact = vi.fn();

    expect(cmp.itemArtifacts({
      sent_artifacts: [{ artifact_id: 'artifact-1', label: 'diff.patch' }],
      returned_artifacts: [{ artifact_id: 'artifact-1', label: 'diff.patch' }, { artifact_id: 'artifact-2', label: 'result.json' }],
      worker_jobs: [{ sent_artifacts: [{ artifact_id: 'artifact-2', label: 'result.json' }] }],
    })).toEqual([
      { artifact_id: 'artifact-1', label: 'diff.patch' },
      { artifact_id: 'artifact-2', label: 'result.json' },
    ]);

    cmp.selectArtifactBySummary({ artifact_id: 'artifact-2' });

    expect(cmp.selectArtifact).toHaveBeenCalledWith('artifact-2');
  });

  it('extracts workspace-relative files from worker job returned refs', () => {
    const cmp = createComponent();
    const files = cmp.itemWorkspaceFiles({
      worker_jobs: [
        {
          worker_job_id: 'job-1',
          worker_url: 'http://alpha:5001',
          returned_refs: [
            { kind: 'workspace_file', artifact_id: 'artifact-1', workspace_relative_path: 'src/app.ts' },
            { kind: 'workspace_file', artifact_id: 'artifact-2', workspace_relative_path: 'README.md' },
            { kind: 'workspace_file', artifact_id: 'artifact-2', workspace_relative_path: 'README.md' },
          ],
        },
      ],
    });

    expect(files).toEqual([
      {
        kind: 'workspace_file',
        workspace_relative_path: 'src/app.ts',
        artifact_id: 'artifact-1',
        filename: undefined,
        worker_job_id: 'job-1',
        worker_url: 'http://alpha:5001',
        worker_name: undefined,
      },
      {
        kind: 'workspace_file',
        workspace_relative_path: 'README.md',
        artifact_id: 'artifact-2',
        filename: undefined,
        worker_job_id: 'job-1',
        worker_url: 'http://alpha:5001',
        worker_name: undefined,
      },
    ]);
  });

  it('aggregates workspace files per worker group', () => {
    const cmp = createComponent();
    cmp.artifactFlowReadModel = {
      items: [
        {
          worker_jobs: [
            {
              worker_job_id: 'job-1',
              worker_url: 'http://alpha:5001',
              returned_refs: [
                { kind: 'workspace_file', artifact_id: 'artifact-1', workspace_relative_path: 'src/alpha.ts' },
              ],
            },
            {
              worker_job_id: 'job-2',
              worker_url: 'http://beta:5002',
              returned_refs: [
                { kind: 'workspace_file', artifact_id: 'artifact-2', workspace_relative_path: 'src/beta.ts' },
              ],
            },
          ],
        },
      ],
    };

    const alphaFiles = cmp.workerWorkspaceFiles({ worker_url: 'http://alpha:5001' });
    const betaFiles = cmp.workerWorkspaceFiles({ worker_url: 'http://beta:5002' });

    expect(alphaFiles.map((file) => file.workspace_relative_path)).toEqual(['src/alpha.ts']);
    expect(betaFiles.map((file) => file.workspace_relative_path)).toEqual(['src/beta.ts']);
  });

  it('derives workspace run candidates from artifact flow jobs and loads workspace files', () => {
    const cmp = createComponent();
    cmp.artifactFlowReadModel = {
      items: [
        {
          task_id: 'task-parent-1',
          title: 'Implement worker feature',
          updated_at: 10,
          worker_jobs: [
            {
              worker_job_id: 'job-1',
              worker_url: 'http://alpha:5001',
              worker_name: 'alpha',
              subtask_id: 'subtask-1',
              updated_at: 20,
            },
          ],
        },
      ],
    };
    agentApiMock.taskWorkspaceFiles.mockReturnValueOnce(of({
      workspace: {
        workspace_dir: '/tmp/worker-runtime/alpha/subtask-1',
        file_count: 2,
        files: [
          { relative_path: 'src/app.ts', size_bytes: 12 },
          { relative_path: 'src/lib/util.ts', size_bytes: 5 },
        ],
      },
    }));

    expect(cmp.workspaceCandidates()).toHaveLength(1);
    cmp.selectedWorkspaceRunKey = cmp.workspaceCandidates()[0].key;

    cmp.loadSelectedWorkspaceFiles();

    expect(agentApiMock.taskWorkspaceFiles).toHaveBeenCalledWith(
      'http://alpha:5001',
      'subtask-1',
      undefined,
      { trackedOnly: true, maxEntries: 4000 },
    );
    expect(cmp.workspaceTreeLines().map((line) => line.path)).toEqual([
      'src',
      'src/app.ts',
      'src/lib',
      'src/lib/util.ts',
    ]);
  });
});
