import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationShellComponent } from './organization-shell.component';

describe('OrganizationShellComponent pending instantiation lifecycle', () => {
  it('blocks route departure and browser unload only while instantiation is pending', () => {
    const instantiationPending = signal(false);
    TestBed.configureTestingModule({
      providers: [
        {
          provide: OrganizationTopologyStateService,
          useValue: { instantiationPending },
        },
      ],
    });
    const component = TestBed.runInInjectionContext(() => new OrganizationShellComponent());
    const safeEvent = unloadEvent();

    expect(component.canLeaveOrganizations()).toBe(true);
    component.preventUnsafeUnload(safeEvent);
    expect(safeEvent.preventDefault).not.toHaveBeenCalled();

    instantiationPending.set(true);
    const pendingEvent = unloadEvent();

    expect(component.canLeaveOrganizations()).toBe(false);
    component.preventUnsafeUnload(pendingEvent);
    expect(pendingEvent.preventDefault).toHaveBeenCalledOnce();
    expect(pendingEvent.returnValue).toBe('');
  });
});

function unloadEvent(): BeforeUnloadEvent & { preventDefault: ReturnType<typeof vi.fn> } {
  return {
    preventDefault: vi.fn(),
    returnValue: undefined,
  } as unknown as BeforeUnloadEvent & { preventDefault: ReturnType<typeof vi.fn> };
}
