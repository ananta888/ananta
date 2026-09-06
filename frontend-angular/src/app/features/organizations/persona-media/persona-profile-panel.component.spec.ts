import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, Subject, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { PersonaProfileApiClient } from './persona-profile-api.client';
import { PersonaProfilePanelComponent } from './persona-profile-panel.component';
import { PersonaProfileSnapshot } from './persona-profile.models';

const blank: PersonaProfileSnapshot = { profile: null, revision: 0, content_hash: null, media_available: true, tenant_id: 'tenant' };
const image = { tenant_id: 'tenant', project_id: 'project', artifact_id: 'image', revision: 1, sha256: 'a'.repeat(64), kind: 'image', classification: 'test_only' } as const;

function setup(current: () => Observable<PersonaProfileSnapshot> = () => of(blank)) {
  const state = {
    hubUrl: signal('https://hub.test'), projectId: signal('project'), selectedOrganizationId: signal('org'),
    topology: signal({ organization_id: 'org', nodes: [
      { kind: 'team', team_id: 'team', label: 'Team A' },
      { kind: 'assignment', assignment_id: 'assignment', label: 'Agent A' },
    ] }),
  };
  const api = { current: vi.fn(current), effective: vi.fn(() => of({ purpose: 'preview', runtime_bound: false, topology_revision: 1, media: [] })), save: vi.fn(() => of({ revision: 1, content_hash: 'b'.repeat(64) })),
    image: vi.fn(() => of(image)), preview: vi.fn(() => of(new Blob(['synthetic-png'], { type: 'image/png' }))),
  };
  TestBed.configureTestingModule({ providers: [
    { provide: OrganizationTopologyStateService, useValue: state }, { provide: PersonaProfileApiClient, useValue: api },
  ] });
  const fixture = TestBed.createComponent(PersonaProfilePanelComponent);
  fixture.detectChanges();
  return { fixture, state, api, facade: fixture.componentInstance.facade };
}

describe('Persona profile panel', () => {
  it('saves an explicit inherited selection without publishing or inventing an asset', () => {
    const { fixture, facade, api } = setup();
    facade.personaId.set('presentation');
    facade.selectImageState('inherit');
    facade.save();
    expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ organization: 'org', owner: 'org' }),
      expect.objectContaining({ revision: 1, persona_id: 'presentation', image: { state: 'inherit', asset: null }, requested_usage: [] }), 0);
    expect(api.preview).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('weder ein Meet-Raum');
    expect(fixture.nativeElement.textContent).toContain('Fallback stoppen');
  });

  it('maps agent profiles to logical assignments, never worker URLs', () => {
    const { fixture, api } = setup();
    fixture.componentInstance.choose('agent:assignment');
    expect(api.current).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'agent', owner: 'assignment' }));
    fixture.componentInstance.choose('agent:http://worker.test');
    expect(api.current).toHaveBeenCalledTimes(2);
  });

  it('cancels old scope reads and refuses saves even before the scope effect flushes', () => {
    const pending = new Subject<PersonaProfileSnapshot>();
    const { fixture, facade, api, state } = setup(() => pending);
    pending.next(blank);
    facade.personaId.set('presentation');
    state.projectId.set('other');
    facade.save();
    expect(api.save).not.toHaveBeenCalled();
    pending.next({ ...blank, revision: 99 });
    expect(facade.snapshot()?.revision).toBe(0);
    fixture.detectChanges();
    expect(facade.snapshot()).toBeNull();
    expect(api.current).toHaveBeenLastCalledWith(expect.objectContaining({ project: 'other' }));
  });

  it('cleans up authenticated preview object URLs when selection or scope changes', () => {
    const create = vi.fn(() => 'blob:synthetic-preview');
    const revoke = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revoke });
    const { fixture, facade, state } = setup();
    facade.selectImageState('asset');
    facade.changeImageId('image');
    facade.inspectImage();
    expect(facade.previewUrl()).toBe('blob:synthetic-preview');
    state.selectedOrganizationId.set('different');
    fixture.detectChanges();
    expect(revoke).toHaveBeenCalledWith('blob:synthetic-preview');
    expect(facade.image()).toBeNull();
    expect(facade.previewUrl()).toBe('');
  });

  it('keeps a revoked profile repairable and reports conflicts without an automatic retry', () => {
    const { facade, api } = setup(() => of({ ...blank, revision: 7, media_available: false }));
    facade.personaId.set('replacement');
    facade.selectImageState('disabled');
    api.save.mockImplementation(() => throwError(() => new Error('synthetic-conflict')));
    facade.save();
    expect(api.save).toHaveBeenCalledOnce();
    expect(api.save.mock.calls[0][2]).toBe(7);
    expect(facade.error()).toContain('Revisionskonflikt');
    expect(facade.busy()).toBe(false);
  });

  it('requires an inspected reference before saving an explicit asset', () => {
    const { facade, api } = setup();
    facade.personaId.set('presentation');
    facade.selectImageState('asset');
    facade.changeImageId('unverified-id');
    facade.save();
    expect(api.save).not.toHaveBeenCalled();
    expect(facade.error()).toContain('zuerst die Bild-ID prüfen');
  });

  it('shows effective inheritance provenance without overwriting the explicit selection', () => {
    const { fixture, facade, api } = setup();
    facade.selectImageState('inherit');
    facade.effective.set({ purpose: 'preview', runtime_bound: false, topology_revision: 3, media: [{
      kind: 'image', state: 'asset', asset: image, available: true, preview_allowed: true, publication_checked: false,
      origins: [{ owner_kind: 'organization', owner_id: 'org', persona_id: 'org-presentation', profile_revision: 5, selection_state: 'asset' }],
    }] });
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:synthetic-inherited' });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('org-presentation');
    expect(fixture.nativeElement.textContent).toContain('Revision 5');
    expect(fixture.nativeElement.textContent).toContain('test_only');
    facade.previewEffective();
    expect(api.preview).toHaveBeenCalledOnce();
    expect(facade.imageState()).toBe('inherit');
    expect(facade.image()).toBeNull();
    expect(api.save).not.toHaveBeenCalled();
  });
});
