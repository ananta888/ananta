import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { WorkspaceSnapshotApiClient } from './workspace-snapshot-api.client';

describe('WorkspaceSnapshotApiClient', () => {
  it('sends relative filenames, project scope and idempotency without a token field', () => {
    TestBed.configureTestingModule({
      providers: [
        WorkspaceSnapshotApiClient,
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'https://hub.example' }] },
        },
      ],
    });
    const client = TestBed.inject(WorkspaceSnapshotApiClient);
    const testing = TestBed.inject(HttpTestingController);
    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' });

    client.upload({
      projectId: 'project-1',
      displayName: 'Workspace One',
      files: [{ file, relativePath: 'workspace/src/hello.txt' }],
      idempotencyKey: 'workspace-snapshot:test-1',
    }).subscribe();

    const request = testing.expectOne(
      'https://hub.example/api/source-control/v1/workspace-snapshots?project_id=project-1',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.headers.get('Idempotency-Key')).toBe('workspace-snapshot:test-1');
    expect(request.request.headers.has('Authorization')).toBe(false);
    const body = request.request.body as FormData;
    expect(body.get('display_name')).toBe('Workspace One');
    expect((body.getAll('files')[0] as File).name).toBe('workspace/src/hello.txt');
    request.flush({
      workspace_id: 'workspace-1',
      state: 'active',
      file_count: 1,
      total_bytes: 5,
      replayed: false,
    });
    testing.verify();
  });
});
