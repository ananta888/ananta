import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';
import { vi } from 'vitest';

import { ControlCenterPolicyApprovalComponent } from './control-center-policy-approval.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

class MockStateFacade {
  sessions$ = new BehaviorSubject<any[]>([{ id: 's1' }]);
  policyDecisions$ = new BehaviorSubject<any[]>([
    { id: 'd1', decision: 'allow', decision_type: 'auto', reason: 'ok', matched_rule_ids: ['R1'] },
    { id: 'd2', decision: 'require_approval', decision_type: 'tool_call_gate', reason: 'tool_call:session_bootstrap', matched_rule_ids: [], action_id: 'approve:s1', tool_call_id: 'tc-1' },
  ]);
  loadSessions = vi.fn();
  loadPolicyDecisions = vi.fn();
  approveAction = vi.fn().mockReturnValue(of({ approved: true }));
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

  it('sends narrow approval from selected pending row', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterPolicyApprovalComponent],
      providers: [{ provide: ControlCenterStateFacade, useClass: MockStateFacade }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterPolicyApprovalComponent);
    const cmp = fixture.componentInstance;
    fixture.detectChanges();

    cmp.selectedSessionId = 's1';
    cmp.selectedPendingId = 'd2';
    cmp.approveSelected();

    const facade = TestBed.inject(ControlCenterStateFacade) as any;
    expect(facade.approveAction).toHaveBeenCalledWith({ action_id: 'approve:s1', tool_call_id: 'tc-1', session_id: 's1', scope: 'single_action' });
    expect(cmp.resultMessage).toContain('erfolgreich');
  });
});
