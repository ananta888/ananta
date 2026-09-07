import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';
import { scope } from './meet-avatar-test-fixtures';
import { MeetVoicePickerComponent } from './meet-voice-picker.component';
import { effectiveVoice } from './meet-voice-test-fixtures';

describe('passive voice picker', () => {
  const api = { effective: vi.fn() }, directory = { list: vi.fn() };
  beforeEach(() => {
    api.effective.mockReset().mockReturnValue(of(effectiveVoice()));
    directory.list.mockReset().mockReturnValue([{ role: 'hub', url: scope.hub }]);
    TestBed.configureTestingModule({ imports: [MeetVoicePickerComponent], providers: [
      { provide: PersonaProfileApiClient, useValue: api }, { provide: AgentDirectoryService, useValue: directory },
    ] });
  });
  function setup() {
    const fixture = TestBed.createComponent(MeetVoicePickerComponent);
    fixture.componentRef.setInput('projectId', 'p'); fixture.detectChanges();
    const component = fixture.componentInstance, emit = vi.spyOn(component.voiceSelected, 'emit');
    return { fixture, component, emit };
  }
  it('loads no media and emits only a copied pin after explicit check and selection', () => {
    const { fixture, component: c, emit } = setup();
    expect(api.effective).not.toHaveBeenCalled(); c.select(); expect(emit).not.toHaveBeenCalled();
    c.edit('organization', 'org'); c.resolve(); expect(api.effective).toHaveBeenCalledWith(scope);
    expect(emit).not.toHaveBeenCalled(); c.select(); expect(emit).toHaveBeenCalledExactlyOnceWith(effectiveVoice().selection);
    expect(fixture.nativeElement.querySelectorAll('img,video,audio').length).toBe(0);
    expect(fixture.nativeElement.textContent).toContain('keine Veröffentlichungsfreigabe');
  });
  it('fences a pending response after project changes and keeps configured selection explicit', () => {
    const { fixture, component: c, emit } = setup(), pending = new Subject<PersonaEffectiveProfile>();
    api.effective.mockReturnValue(pending); c.organization = 'org'; c.resolve();
    c.configured(); expect(emit).not.toHaveBeenCalled();
    fixture.componentRef.setInput('projectId', 'other'); fixture.detectChanges();
    pending.next(effectiveVoice()); expect(c.candidate()).toBeNull(); expect(c.busy()).toBe(false);
    c.configured(); expect(emit).toHaveBeenCalledExactlyOnceWith(null);
  });
  it('clears selected metadata after owner, Hub or enabled-state changes', () => {
    const { fixture, component: c, emit } = setup(); c.organization = 'org'; c.resolve();
    c.chooseKind('team'); c.select(); expect(emit).not.toHaveBeenCalled();
    c.chooseKind('organization'); c.resolve(); directory.list.mockReturnValue([{ role: 'hub', url: 'https://other.test' }]);
    c.select(); expect(c.candidate()).toBeNull();
    fixture.componentRef.setInput('disabled', true); fixture.detectChanges(); c.resolve(); c.configured();
    expect(emit).not.toHaveBeenCalled();
  });
  it('does not choose a fallback when the profile policy denies preview', () => {
    const { component: c, emit } = setup(); c.organization = 'org';
    api.effective.mockReturnValue(throwError(() => ({ status: 403 }))); c.resolve();
    expect(c.busy()).toBe(false); expect(c.message()).toContain('Keine Ersatzquelle'); expect(emit).not.toHaveBeenCalled();
  });
});
