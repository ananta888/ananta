import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';

import { ControlCenterSessionsComponent } from './control-center-sessions.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { ControlCenterEventStreamService } from '../services/control-center-event-stream.service';

class MockStateFacade {
  sessions$ = new BehaviorSubject<any[]>([{ id: 's1', task_id: 't1', owner_user_id: 'u1', transport: 'hub_relay', status: 'running' }]);
  taskVerificationById$ = new BehaviorSubject<any>({ t1: { status: 'passed', test_count: 10, passed_count: 10, failed_count: 0 } });
  loadSessions = jasmine.createSpy('loadSessions');
  connectEvents = jasmine.createSpy('connectEvents');
  disconnectEvents = jasmine.createSpy('disconnectEvents');
  loadTaskDetailVerification = jasmine.createSpy('loadTaskDetailVerification');
}

class MockStreamService {
  state$ = new BehaviorSubject<any>('connected');
  lastHeartbeatAt$ = new BehaviorSubject<number>(Date.now());
}

describe('ControlCenterSessionsComponent', () => {
  it('renders session and uses verification map', async () => {
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
    expect(cmp.verificationFor('t1')?.status).toBe('passed');
  });
});
