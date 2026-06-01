import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';

import { ControlCenterPolicyApprovalComponent } from './control-center-policy-approval.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

class MockStateFacade {
  sessions$ = new BehaviorSubject<any[]>([{ id: 's1' }]);
  policyDecisions$ = new BehaviorSubject<any[]>([
    { id: 'd1', decision: 'allow', decision_type: 'auto', reason: 'ok', matched_rule_ids: ['R1'] },
  ]);
  loadSessions = jasmine.createSpy('loadSessions');
  loadPolicyDecisions = jasmine.createSpy('loadPolicyDecisions');
  approveAction = jasmine.createSpy('approveAction').and.returnValue(of({ approved: true }));
}

describe('ControlCenterPolicyApprovalComponent', () => {
  it('renders decision log from backend facade state', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterPolicyApprovalComponent],
      providers: [{ provide: ControlCenterStateFacade, useClass: MockStateFacade }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterPolicyApprovalComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;

    expect(text).toContain('Decision Log');
    expect(text).toContain('ok');
  });

  it('sends narrow approval with action+tool_call+session', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterPolicyApprovalComponent],
      providers: [{ provide: ControlCenterStateFacade, useClass: MockStateFacade }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterPolicyApprovalComponent);
    const cmp = fixture.componentInstance;
    fixture.detectChanges();

    cmp.selectedSessionId = 's1';
    cmp.pendingActionId = 'a-1';
    cmp.pendingToolCallId = 'tc-1';
    cmp.approve();

    const facade = TestBed.inject(ControlCenterStateFacade) as any;
    expect(facade.approveAction).toHaveBeenCalledWith({ action_id: 'a-1', tool_call_id: 'tc-1', session_id: 's1' });
    expect(cmp.resultMessage).toContain('erfolgreich');
  });
});
