import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of, throwError } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { SemanticMediaDebugApiService } from '../../services/semantic-media-debug-api.service';
import { ShareSessionService } from '../../services/share-session.service';
import { SemanticDebugHostComponent } from './semantic-debug-host.component';

const state$ = new BehaviorSubject<any>({
  session: { id: 'session-a' }, participants: [], messages: [], cursor: '0', role: 'owner',
});
const page = {
  items: [{
    event_id: 'audit-a', scope_digest: 'a'.repeat(64), event_type: 'semantic_contract',
    transition: 'activated', reason_code: 'hub_confirmed', epoch: 3,
    contract_ref: 'b'.repeat(64), lease_ref: null, job_ref: null,
    created_at_ms: 1_000, expires_at_ms: 2_000,
  }],
  nextCursor: null,
};

describe('SemanticDebugHostComponent', () => {
  const api = { page: vi.fn() };

  beforeEach(() => {
    api.page.mockReset().mockReturnValue(of(page));
    state$.next({ session: { id: 'session-a' }, participants: [], messages: [], cursor: '0', role: 'owner' });
    TestBed.configureTestingModule({
      imports: [SemanticDebugHostComponent],
      providers: [
        { provide: SemanticMediaDebugApiService, useValue: api },
        { provide: ShareSessionService, useValue: { state$ } },
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', name: 'hub', url: 'http://hub.test' }] } },
      ],
    });
  });

  it('binds the active Pair session to contract and media audit scopes', async () => {
    const fixture = TestBed.createComponent(SemanticDebugHostComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.loading).toBe(false));
    fixture.detectChanges();
    expect(api.page).toHaveBeenCalledWith('http://hub.test', 'semantic-contract:session-a', null);
    expect(api.page).toHaveBeenCalledWith('http://hub.test', 'semantic-media-session:session-a', null);
    expect(fixture.nativeElement.textContent).toContain('semantic_contract · activated');
  });

  it('surfaces role denial without exposing a mutation control', async () => {
    api.page.mockReturnValue(throwError(() => ({ error: { error: { code: 'semantic_debug_forbidden' } } })));
    const fixture = TestBed.createComponent(SemanticDebugHostComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.loading).toBe(false));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('semantic_debug_forbidden');
    expect(fixture.nativeElement.querySelectorAll('input, textarea, select').length).toBe(0);
  });
});
