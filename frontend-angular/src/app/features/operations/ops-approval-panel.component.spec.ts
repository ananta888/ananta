import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { ApprovalsApiService } from '../../services/approvals-api.service';
import { OpsApprovalPanelComponent } from './ops-approval-panel.component';

describe('OpsApprovalPanelComponent', () => {
  const request = { request_id: 'APR-1', tool_name: 'git.push', digest_prefix: 'abcdef123456', risk_class: 'high', status: 'pending' };
  const api = {
    listRequests: vi.fn(() => of({ requests: [request, { ...request, request_id: 'OTHER', tool_name: 'unrelated.tool' }] })),
    decide: vi.fn(() => of({ ...request, status: 'granted' })),
  };

  beforeEach(() => TestBed.configureTestingModule({ imports: [OpsApprovalPanelComponent], providers: [{ provide: ApprovalsApiService, useValue: api }] }));

  it('shows only Git, Docker and Compose approvals', () => {
    const fixture = TestBed.createComponent(OpsApprovalPanelComponent);
    fixture.componentRef.setInput('baseUrl', 'http://hub');
    fixture.detectChanges();

    expect(fixture.componentInstance.requests.map((row) => row.request_id)).toEqual(['APR-1']);
    expect(fixture.nativeElement.textContent).toContain('git.push');
    expect(fixture.nativeElement.textContent).not.toContain('unrelated.tool');
  });

  it('grants a digest-bound request but leaves execution to an explicit retry', () => {
    const fixture = TestBed.createComponent(OpsApprovalPanelComponent);
    const decided = vi.fn();
    fixture.componentInstance.baseUrl = 'http://hub';
    fixture.componentInstance.requests = [request as any];
    fixture.componentInstance.decided.subscribe(decided);

    fixture.componentInstance.decide(request as any, 'granted');

    expect(api.decide).toHaveBeenCalledWith('http://hub', 'APR-1', 'granted', undefined);
    expect(decided).toHaveBeenCalledWith({ request, decision: 'granted' });
    expect(fixture.componentInstance.message).toContain('erneut ausgelöst');
  });
});

