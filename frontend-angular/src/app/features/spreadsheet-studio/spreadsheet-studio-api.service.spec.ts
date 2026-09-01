import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';

describe('SpreadsheetStudioApiService', () => {
  const core = {
    get: vi.fn(),
    post: vi.fn(),
    request: vi.fn(),
    requestBlob: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        SpreadsheetStudioApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('imports the original file through the authenticated Hub boundary', async () => {
    core.request.mockReturnValue(of({ document_id: 'document-a' }));
    const api = TestBed.inject(SpreadsheetStudioApiService);
    const file = new File(['sheet'], 'finance.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });

    await firstValueFrom(api.importDocument('http://hub.test/', file, 'Finance'));

    expect(core.request).toHaveBeenCalledWith(
      'POST',
      'http://hub.test/api/spreadsheet-studio/documents/import',
      'http://hub.test/',
      expect.objectContaining({ timeoutMs: 120_000 }),
    );
    const form = core.request.mock.calls[0][3].body as FormData;
    expect(form.get('title')).toBe('Finance');
    expect(form.get('file')).toBeInstanceOf(File);
  });

  it('keeps consent revocation and dataset materialization on Hub-owned endpoints', async () => {
    core.post.mockReturnValue(of({ state: 'revoked' }));
    const api = TestBed.inject(SpreadsheetStudioApiService);

    await firstValueFrom(api.revokeConsent('http://hub.test', 'consent/a', 3));
    await firstValueFrom(api.materializeDataset('http://hub.test', { schema: 'command' }));

    expect(core.post.mock.calls.map(call => `${call[0]}:${JSON.stringify(call[1])}`)).toEqual([
      'http://hub.test/api/spreadsheet-studio/consents/consent%2Fa/revoke:{"expected_version":3}',
      'http://hub.test/api/spreadsheet-studio/datasets/materialize:{"schema":"command"}',
    ]);
  });

  it('starts ML-Intern training with replay protection and a bounded timeout', async () => {
    core.request.mockReturnValue(of({ job: { state: 'queued' } }));
    const api = TestBed.inject(SpreadsheetStudioApiService);
    const command = { schema: 'ananta.spreadsheet-training-command.v1', dataset_id: 'dataset/a' };

    await firstValueFrom(api.startTraining('http://hub.test', 'dataset/a', command, 'training-key'));

    expect(core.request).toHaveBeenCalledWith(
      'POST',
      'http://hub.test/api/spreadsheet-studio/datasets/dataset%2Fa/training',
      'http://hub.test',
      {
        body: command,
        headers: { 'Idempotency-Key': 'training-key' },
        timeoutMs: 120_000,
      },
    );
  });

  it('requests inference as a proposal and never sends an apply flag', async () => {
    core.post.mockReturnValue(of({ automatic_apply: false }));
    const api = TestBed.inject(SpreadsheetStudioApiService);
    const command = { schema: 'ananta.spreadsheet-inference-command.v1' };

    await firstValueFrom(api.infer('http://hub.test', command));

    expect(core.post).toHaveBeenCalledWith(
      'http://hub.test/api/spreadsheet-studio/inference/proposals',
      command,
      'http://hub.test',
      undefined,
      false,
      120_000,
    );
    expect(command).not.toHaveProperty('automatic_apply');
  });
});
