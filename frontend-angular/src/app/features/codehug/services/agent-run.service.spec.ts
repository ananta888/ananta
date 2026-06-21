import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { firstValueFrom, of } from 'rxjs';

import { AgentRunService } from './agent-run.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ChServiceError } from '../models/codehug.models';

function mockHubCore(responses: Record<string, any> = {}) {
  return {
    get: vi.fn((url: string) => {
      const key = Object.keys(responses).find(k => url.includes(k));
      return key ? of(responses[key]) : of({});
    }),
    post: vi.fn((url: string, body: any) => {
      const key = Object.keys(responses).find(k => url.includes(k));
      return key ? of(responses[key]) : of({});
    }),
    patch: vi.fn(),
    delete: vi.fn(),
  };
}

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

describe('AgentRunService', () => {
  let service: AgentRunService;
  let hubCore: ReturnType<typeof mockHubCore>;

  beforeEach(() => {
    hubCore = mockHubCore({
      '/api/agent-runs': { run_id: 'r-123' },
    });
    TestBed.configureTestingModule({
      providers: [
        AgentRunService,
        { provide: HubApiCoreService, useValue: hubCore },
        { provide: AgentDirectoryService, useValue: mockDir() },
      ],
    });
    service = TestBed.inject(AgentRunService);
  });

  it('should be created', () => expect(service).toBeTruthy());

  it('startRun: posts write_armed=false by default', async () => {
    const resp = await firstValueFrom(service.startRun({
      projectId: 'p1',
      profileId: 'prof-1',
      taskDescription: 'refactor',
      riskLevel: 'low',
      writeArmed: false,
    }));
    expect(resp.runId).toBe('r-123');
    const body = (hubCore.post as any).mock.calls[0][1];
    expect(body.write_armed).toBe(false);
  });

  it('startRun: propagates write_armed=true explicitly', async () => {
    await firstValueFrom(service.startRun({
      projectId: 'p1',
      profileId: 'prof-1',
      taskDescription: 'fix',
      riskLevel: 'high',
      writeArmed: true,
    }));
    const body = (hubCore.post as any).mock.calls[0][1];
    expect(body.write_armed).toBe(true);
  });

  it('applyDiff: rejects when applyConfirmationToken is missing (sync throw)', () => {
    expect(() => service.applyDiff({
      runId: 'r-1',
      acceptedFilePaths: ['a.py'],
      applyConfirmationToken: '',
    })).toThrow(ChServiceError);
  });

  it('applyDiff: sends confirmation token', async () => {
    hubCore.post = vi.fn(() => of({ applied: ['a.py'], failed: [], verification_triggered: true }));
    const resp = await firstValueFrom(service.applyDiff({
      runId: 'r-1',
      acceptedFilePaths: ['a.py'],
      applyConfirmationToken: 'tok-abc',
    }));
    expect(resp.applied).toEqual(['a.py']);
    const body = (hubCore.post as any).mock.calls[0][1];
    expect(body.apply_confirmation_token).toBe('tok-abc');
  });

  it('getRun: normalizes step phases', async () => {
    hubCore.get = vi.fn(() => of({
      id: 'r-1', status: 'running',
      project_id: 'p1', profile_id: 'prof-1',
      started_at: 1, finished_at: null, duration_ms: null,
      write_armed: false,
      actual_cli_backend: 'sgpt', actual_model: 'llama3', actual_provider: 'ollama',
      deterministic_step_count: 2, llm_step_count: 1,
      routing_reason: 'routing matched sgpt profile',
      policy_snapshot_id: null, warnings: [],
      steps: [
        { id: 's1', index: 0, phase: 'plan', title: 'plan', started_at: 1, finished_at: 2, duration_ms: 1000, status: 'succeeded', worker_id: 'w-1', cli_backend: 'sgpt', model: 'llama3' },
        { id: 's2', index: 1, phase: 'det', title: 'check', started_at: 2, finished_at: 3, duration_ms: 1000, status: 'succeeded' },
      ],
    }));
    const run = await firstValueFrom(service.getRun('r-1'));
    expect(run.steps).toHaveLength(2);
    expect(run.steps[0].phase).toBe('plan');
    expect(run.steps[0].cliBackend).toBe('sgpt');
    expect(run.steps[1].phase).toBe('det');
    expect(run.actualCliBackend).toBe('sgpt');
  });
});