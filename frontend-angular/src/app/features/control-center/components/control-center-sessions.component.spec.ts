import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';

import { ControlCenterSessionsComponent } from './control-center-sessions.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { ControlCenterEventStreamService } from '../services/control-center-event-stream.service';

class MockStateFacade {
  sessions$ = new BehaviorSubject<any[]>([{
    id: 's1',
    task_id: 't1',
    owner_user_id: 'u1',
    transport: 'hub_relay',
    status: 'running',
    worker_id: 'worker-1',
    worker_type: 'codex',
    model: 'gpt-test',
    runtime: 'local',
    policy_snapshot: {
      risk_level: 'low',
      allowed_tools: ['read_file'],
      denied_tools: [],
      allowed_paths: ['/agent/**'],
      denied_paths: ['/.env'],
      cloud_allowed: false,
      runtime_boundary: 'local-only',
      requires_human_approval: false,
      approval_reason: null,
      policy_version: 'v2',
    },
  }]);
  taskVerificationById$ = new BehaviorSubject<any>({ t1: { status: 'passed', test_count: 10, passed_count: 10, failed_count: 0 } });
  toolCallsBySessionId$ = new BehaviorSubject<any>({});
  loadSessions = vi.fn();
  connectEvents = vi.fn();
  disconnectEvents = vi.fn();
  loadTaskDetailVerification = vi.fn();
  loadSessionToolCalls = vi.fn();
}

class MockStreamService {
  state$ = new BehaviorSubject<any>('connected');
  lastHeartbeatAt$ = new BehaviorSubject<number>(Date.now());
}

describe('ControlCenterSessionsComponent', () => {
  it.skip('renders backend-bound session values and uses verification map', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterSessionsComponent],
      providers: [
        { provide: ControlCenterStateFacade, useClass: MockStateFacade },
        { provide: ControlCenterEventStreamService, useClass: MockStreamService },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterSessionsComponent);
    fixture.detectChanges();
    const cmp = fixture.componentInstance;

    expect(cmp.sessions.length).toBe(1);
    expect(cmp.sessions[0].workerType).toBe('codex');
    expect(cmp.sessions[0].model).toBe('gpt-test');
    expect(cmp.sessions[0].policySnapshot.policyVersion).toBe('v2');
    expect(cmp.verificationFor('t1')?.status).toBe('passed');
  });
});
