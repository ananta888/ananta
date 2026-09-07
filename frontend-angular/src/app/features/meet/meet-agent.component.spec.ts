import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetApiService } from './meet-api.service';
import { MeetAgentComponent } from './meet-agent.component';

const result = { schema: 'ananta.meet-turn-result.v1', text: 'Hallo',
  video: { mime: 'video/mp4', base64: btoa('synthetic-test-video') } };

describe('Local Meet media assistant', () => {
  const api = { turn: vi.fn() };
  let identity: BehaviorSubject<unknown>;
  beforeEach(() => {
    identity = new BehaviorSubject({ sub: 'alice' });
    api.turn.mockReset().mockReturnValue(of(result));
    vi.stubGlobal('URL', class extends URL {
      static override createObjectURL = vi.fn(() => 'blob:synthetic-video');
      static override revokeObjectURL = vi.fn();
    });
    TestBed.configureTestingModule({ imports: [MeetAgentComponent], providers: [
      { provide: MeetApiService, useValue: api },
      { provide: UserAuthService, useValue: { user$: identity } },
    ] });
  });
  afterEach(() => { TestBed.resetTestingModule(); vi.unstubAllGlobals(); });

  function setup() {
    const fixture = TestBed.createComponent(MeetAgentComponent);
    fixture.componentRef.setInput('projectId', 'project'); fixture.detectChanges();
    return fixture;
  }

  it('never starts generation or human capture on load', () => {
    const fixture = setup();
    expect(api.turn).not.toHaveBeenCalled();
    expect(fixture.nativeElement.querySelector('video')).toBeNull();
  });

  it('renders text and bounded video and releases them on stop', () => {
    const fixture = setup(), component = fixture.componentInstance;
    component.prompt = 'Hallo'; component.send(); fixture.detectChanges();
    expect(api.turn).toHaveBeenCalledWith('project', 'Hallo');
    expect(fixture.nativeElement.textContent).toContain('Ananta (KI): Hallo');
    expect(fixture.nativeElement.querySelector('video').autoplay).toBe(false);
    component.clear(); fixture.detectChanges();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-video');
    expect(fixture.nativeElement.querySelector('video')).toBeNull();
  });

  it.each(['account', 'project', 'destroy'])('discards late results on %s change', change => {
    const response = new Subject(); api.turn.mockReturnValue(response);
    const fixture = setup(), component = fixture.componentInstance;
    component.prompt = 'Hallo'; component.send();
    if (change === 'account') identity.next(null);
    else if (change === 'project') { fixture.componentRef.setInput('projectId', 'other'); fixture.detectChanges(); }
    else fixture.destroy();
    response.next(result);
    expect(response.observed).toBe(false);
    expect(component.reply()).toBe('');
  });

  it.each([403, 404, 503])('bounds denial/failure %s', status => {
    api.turn.mockReturnValue(throwError(() => ({ status })));
    const component = setup().componentInstance;
    component.prompt = 'Hallo'; component.send();
    expect(component.busy()).toBe(false); expect(component.error()).not.toBe('');
  });

  it('rejects executable or malformed media payloads', () => {
    api.turn.mockReturnValue(of({ ...result, video: { mime: 'text/html', base64: 'a' } }));
    const component = setup().componentInstance;
    component.prompt = 'Hallo'; component.send();
    expect(component.videoUrl()).toBe(''); expect(component.error()).not.toBe('');
  });

  it('keeps a task-bound publication on the task endpoint', () => {
    const fixture = setup();
    fixture.componentRef.setInput('taskId', 'meeting-task'); fixture.detectChanges();
    const component = fixture.componentInstance;
    component.publishToMeet = true; component.prompt = 'Hallo'; component.send();
    expect(api.turn).toHaveBeenCalledWith('project', 'Hallo', true, 'meeting-task');
  });

  it.each(['loop', 'hold_last'] as const)('requests an explicitly selected clip in %s mode without automatic publication', async repeatMode => {
    const fixture = setup(), component = fixture.componentInstance;
    // Exercise Angular's actual form event path, not an unnotified assignment
    // to a plain component field in a zoneless change-detection test.
    const mode = fixture.nativeElement.querySelector('select') as HTMLSelectElement;
    mode.value = 'clip'; mode.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    const id = fixture.nativeElement.querySelector('input[maxlength="160"]') as HTMLInputElement;
    id.value = 'clip'; id.dispatchEvent(new Event('input'));
    const repeat = fixture.nativeElement.querySelectorAll('select')[1] as HTMLSelectElement;
    repeat.value = repeatMode; repeat.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    expect(api.turn).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('keine generative Animation');
    component.prompt = 'Hallo'; component.send();
    expect(api.turn).toHaveBeenCalledWith('project', 'Hallo', false, '', {
      kind: 'stored_clip', artifactId: 'clip', repeatMode,
    });
  });

  it('does not infer a repeat mode from selecting a clip', () => {
    const component = setup().componentInstance;
    component.visualMode = 'clip'; component.clipId = 'clip'; component.prompt = 'Hallo';
    component.send();
    expect(api.turn).not.toHaveBeenCalled();
    expect(component.busy()).toBe(false);
    expect(component.error()).toContain('ausdrücklich auswählen');
  });

  it.each(['', '/tmp/private.mp4', 'https://foreign.test/clip'])('rejects an invalid clip ID without generation', id => {
    const component = setup().componentInstance;
    component.visualMode = 'clip'; component.clipId = id; component.clipRepeatMode = 'loop'; component.prompt = 'Hallo';
    component.send();
    expect(api.turn).not.toHaveBeenCalled();
    expect(component.error()).toContain('Clip-ID');
  });

  it('clears clip selection and repeat settings on account change', () => {
    const component = setup().componentInstance;
    component.visualMode = 'clip'; component.clipId = 'clip'; component.clipRepeatMode = 'loop';
    identity.next(null);
    expect(component.visualMode).toBe('avatar');
    expect(component.clipId).toBe('');
    expect(component.clipRepeatMode).toBe('');
    expect(api.turn).not.toHaveBeenCalled();
  });
});
