import { TestBed } from '@angular/core/testing';
import { firstValueFrom, take, toArray } from 'rxjs';

import { UserAuthService } from './user-auth.service';
import {
  WORKFLOW_STREAM_FRAME_SCHEMA,
  WorkflowRuntimeStreamService,
} from './workflow-runtime-stream.service';

describe('WorkflowRuntimeStreamService', () => {
  const auth = { token: 'hub-user-token' };
  const frame = {
    schema: WORKFLOW_STREAM_FRAME_SCHEMA,
    event_type: 'workflow.node.started',
    workflow_id: 'workflow-1',
    run_id: 'run-1',
    step_id: 'step-1',
    cursor: 'v1:1',
    event_id: 'event-1',
    occurred_at: 100,
    payload: {},
  } as const;

  beforeEach(() => {
    vi.restoreAllMocks();
    TestBed.configureTestingModule({
      providers: [
        WorkflowRuntimeStreamService,
        { provide: UserAuthService, useValue: auth },
      ],
    });
  });

  it('posts the cursor contract with bearer auth and never puts payload in the URL', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      `${JSON.stringify(frame)}\n`,
      {
        status: 200,
        headers: {
          'Content-Type': 'application/x-ndjson',
          'X-Workflow-Next-Cursor': 'v1:1',
          'X-Workflow-Has-More': 'false',
        },
      },
    ));
    const service = TestBed.inject(WorkflowRuntimeStreamService);

    const page = await service.readPage('http://hub.test/', 'workflow-1', { afterCursor: 'v1:0' });

    expect(page.frames).toEqual([frame]);
    expect(page.nextCursor).toBe('v1:1');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://hub.test/api/visual-process/workflow/events/stream');
    expect(String(url)).not.toContain('workflow-1');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer hub-user-token' });
    expect(JSON.parse(String(init?.body))).toMatchObject({
      workflow_id: 'workflow-1',
      after_cursor: 'v1:0',
    });
  });

  it('disconnect aborts polling after delivering the current canonical frame', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      `${JSON.stringify(frame)}\n`,
      {
        status: 200,
        headers: {
          'X-Workflow-Next-Cursor': 'v1:1',
          'X-Workflow-Has-More': 'false',
        },
      },
    ));
    const service = TestBed.inject(WorkflowRuntimeStreamService);

    const received = await firstValueFrom(service.connect(
      'http://hub.test',
      'workflow-1',
      { heartbeatSeconds: 30 },
    ).pipe(take(1)));

    expect(received).toEqual(frame);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('rejects frames bound to another workflow', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      `${JSON.stringify({ ...frame, workflow_id: 'workflow-foreign' })}\n`,
      { status: 200 },
    ));
    const service = TestBed.inject(WorkflowRuntimeStreamService);

    await expect(service.readPage('http://hub.test', 'workflow-1')).rejects.toThrow(
      'workflow_stream_frame_invalid',
    );
  });

  it('resumes by cursor without displaying a duplicate event', async () => {
    const second = { ...frame, cursor: 'v1:2', event_id: 'event-2' };
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(`${JSON.stringify(frame)}\n`, {
        status: 200,
        headers: {
          'X-Workflow-Next-Cursor': 'v1:1',
          'X-Workflow-Has-More': 'true',
        },
      }))
      .mockResolvedValueOnce(new Response(
        `${JSON.stringify(frame)}\n${JSON.stringify(second)}\n`,
        {
          status: 200,
          headers: {
            'X-Workflow-Next-Cursor': 'v1:2',
            'X-Workflow-Has-More': 'false',
          },
        },
      ));
    const service = TestBed.inject(WorkflowRuntimeStreamService);

    const received = await firstValueFrom(service.connect(
      'http://hub.test',
      'workflow-1',
      { heartbeatSeconds: 30 },
    ).pipe(take(2), toArray()));

    expect(received.map(item => item.event_id)).toEqual(['event-1', 'event-2']);
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });

  it('fails closed when stream authentication expires', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 401 }));
    const service = TestBed.inject(WorkflowRuntimeStreamService);

    await expect(service.readPage('http://hub.test', 'workflow-1')).rejects.toThrow(
      'workflow_stream_http_401',
    );
  });

  it('uses the same bearer-authenticated Hub command for cancellation', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{}', { status: 200 }),
    );
    const service = TestBed.inject(WorkflowRuntimeStreamService);

    await service.cancel('http://hub.test/', 'workflow-1', 'operator_cancelled');

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      'http://hub.test/api/visual-process/workflow/workflow-1/cancel',
    );
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer hub-user-token' });
    expect(JSON.parse(String(init?.body))).toEqual({ reason: 'operator_cancelled' });
  });
});
