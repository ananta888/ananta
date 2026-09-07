import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';
import { MeetAvatarPickerComponent } from './meet-avatar-picker.component';
import { effective, scope } from './meet-avatar-test-fixtures';

describe('passive Hub avatar picker', () => {
  const api = { effective: vi.fn() }, directory = { list: vi.fn() };
  beforeEach(() => {
    api.effective.mockReset().mockReturnValue(of(effective()));
    directory.list.mockReset().mockReturnValue([{ role: 'hub', url: scope.hub }]);
    TestBed.configureTestingModule({ imports: [MeetAvatarPickerComponent], providers: [
      { provide: PersonaProfileApiClient, useValue: api }, { provide: AgentDirectoryService, useValue: directory },
    ] });
  });
  function setup() {
    const fixture = TestBed.createComponent(MeetAvatarPickerComponent);
    fixture.componentRef.setInput('projectId', 'p'); fixture.detectChanges();
    const component = fixture.componentInstance, emit = vi.spyOn(component.avatarSelected, 'emit');
    return { fixture, component, emit };
  }
  it('does nothing on load and emits a copied Hub pin only after two explicit actions', () => {
    const { component: c, emit } = setup(); expect(api.effective).not.toHaveBeenCalled();
    c.select(); expect(emit).not.toHaveBeenCalled(); c.edit('organization', 'org'); c.resolve();
    expect(api.effective).toHaveBeenCalledWith(scope); expect(emit).not.toHaveBeenCalled();
    c.select(); expect(emit).toHaveBeenCalledWith(effective().selection);
  });
  it('keeps neutral selection explicit and cancels a pending profile on context change', () => {
    const { component: c, fixture, emit } = setup(), pending = new Subject<PersonaEffectiveProfile>();
    api.effective.mockReturnValue(pending); c.organization = 'org'; c.resolve();
    c.neutral(); expect(emit).not.toHaveBeenCalled();
    fixture.componentRef.setInput('projectId', 'other'); fixture.detectChanges();
    pending.next(effective()); expect(c.candidate()).toBeNull(); expect(c.busy()).toBe(false);
    c.neutral(); expect(emit).toHaveBeenCalledExactlyOnceWith(null);
  });
  it('clears candidates after edits, hub changes or disabled controls', () => {
    const { component: c, fixture, emit } = setup(); c.organization = 'org'; c.resolve();
    c.edit('owner', 'changed'); c.select(); expect(emit).not.toHaveBeenCalled();
    c.resolve(); directory.list.mockReturnValue([{ role: 'hub', url: 'https://other.test' }]);
    c.select(); expect(c.candidate()).toBeNull(); expect(emit).not.toHaveBeenCalled();
    fixture.componentRef.setInput('disabled', true); fixture.detectChanges();
    c.resolve(); c.neutral(); expect(emit).not.toHaveBeenCalled();
  });
  it('shows bounded failure without automatically selecting neutral', () => {
    const { component: c, emit } = setup(); c.organization = 'org';
    api.effective.mockReturnValue(throwError(() => ({ status: 403 }))); c.resolve();
    expect(c.busy()).toBe(false); expect(c.candidate()).toBeNull(); expect(c.message()).toContain('Keine Ersatzquelle');
    expect(emit).not.toHaveBeenCalled();
  });
  it('renders labelled headless controls and the distinction between checking and publication', () => {
    const { fixture } = setup(); expect(fixture.nativeElement.textContent).toContain('keine Veröffentlichungsfreigabe');
    expect(fixture.nativeElement.querySelectorAll('label').length).toBe(2);
    expect(fixture.nativeElement.querySelectorAll('img,video,audio').length).toBe(0);
  });
});
