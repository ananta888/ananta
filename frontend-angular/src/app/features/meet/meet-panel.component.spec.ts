import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetApiService } from './meet-api.service';
import { MeetPanelComponent } from './meet-panel.component';

const response = {
  schema: 'ananta.meet-binding.v1', project_id: 'project-a', task_id: null,
  revision: 1, invite_url: 'https://webrtc.ananta.de/?room=room-0123456789abcdef01&mode=room',
  room_verified: false, membership_granted: false,
  profile: { origin: 'https://webrtc.ananta.de', create_url: 'https://webrtc.ananta.de/', creation_mode: 'meet_ui_then_attach' },
};

describe('Meet panel', () => {
  const api = { binding: vi.fn() };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    identity = new BehaviorSubject({ sub: 'alice' });
    api.binding.mockReset().mockReturnValue(of(response));
    TestBed.configureTestingModule({
      imports: [MeetPanelComponent], providers: [
        { provide: MeetApiService, useValue: api },
        { provide: UserAuthService, useValue: { user$: identity } },
      ],
    });
  });

  it('renders safe external navigation and never creates a room or captures on load', () => {
    const fixture = TestBed.createComponent(MeetPanelComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();
    const links: HTMLAnchorElement[] = Array.from(fixture.nativeElement.querySelectorAll('a'));
    expect(links.length).toBe(2);
    expect(links.every(link => link.target === '_blank' && link.rel === 'noopener noreferrer')).toBe(true);
    expect(api.binding).toHaveBeenCalledExactlyOnceWith('project-a', '', 'GET', undefined);
    expect(fixture.nativeElement.querySelector('iframe')).toBeNull();
  });

  it('attaches and unlinks with the current revision', () => {
    const fixture = TestBed.createComponent(MeetPanelComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.invite.set(response.invite_url);
    component.attach();
    expect(api.binding).toHaveBeenLastCalledWith('project-a', '', 'PUT', {
      invite_url: response.invite_url, expected_revision: 1,
    });
    component.unlink();
    expect(api.binding).toHaveBeenLastCalledWith('project-a', '', 'DELETE', { expected_revision: 1 });
  });

  it.each([401, 403, 404, 409, 500])('clears capabilities after denial or failure %s', status => {
    const fixture = TestBed.createComponent(MeetPanelComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();
    api.binding.mockReturnValue(throwError(() => ({ status })));
    fixture.componentInstance.reload();
    fixture.detectChanges();
    expect(fixture.componentInstance.binding()).toBeNull();
    expect(fixture.nativeElement.querySelector('a')).toBeNull();
    expect(fixture.componentInstance.busy()).toBe(false);
  });

  it('cancels requests and clears pasted capabilities on context changes', () => {
    const first = new Subject();
    api.binding.mockReturnValueOnce(first).mockReturnValueOnce(of({ ...response, project_id: 'project-b' }));
    const fixture = TestBed.createComponent(MeetPanelComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();
    fixture.componentInstance.invite.set(response.invite_url);
    fixture.componentRef.setInput('projectId', 'project-b');
    fixture.detectChanges();
    first.next(response);
    expect(fixture.componentInstance.binding()?.project_id).toBe('project-b');
    expect(fixture.componentInstance.invite()).toBe('');
    expect(first.observed).toBe(false);
  });

  it('clears old room links when the authenticated account changes', () => {
    const fixture = TestBed.createComponent(MeetPanelComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();
    api.binding.mockReturnValue(new Subject());
    identity.next(null);
    fixture.detectChanges();
    expect(fixture.componentInstance.binding()).toBeNull();
    expect(fixture.nativeElement.querySelector('a')).toBeNull();
  });
});
