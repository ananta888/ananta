import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PairSessionCatalogComponent } from './pair-session-catalog.component';
import {
  ActiveShareState,
  ShareSession,
  ShareSessionCatalogEntry,
  ShareSessionService,
} from '../services/share-session.service';

function session(id: string, title: string, createdAt: number): ShareSession {
  return {
    id,
    title,
    invite_code: `${id}-invite`,
    mode: 'p2p',
    transport: 'webrtc',
    permissions: {
      chat: true,
      view_tui: true,
      remote_cursor: true,
      artifact_share: false,
      remote_control: false,
    },
    created_at: createdAt,
    expires_at: null,
    revoked_at: null,
    owner_user_id: 'owner',
    owner_peer_id: `${id}-private-owner-peer`,
    security_epoch: 3,
    security_contract_version: 1,
    security_mode: 'strict_e2ee',
  };
}

function configure(entries: readonly ShareSessionCatalogEntry[]) {
  TestBed.resetTestingModule();
  const state$ = new BehaviorSubject<ActiveShareState>({
    session: entries[0]?.session ?? null,
    role: entries[0]?.role ?? null,
    participants: [],
    messages: [],
    cursor: '0',
  });
  const service = {
    state$,
    listSessions: vi.fn(async () => entries),
    switchToSession: vi.fn(async (sessionId: string) => {
      const selected = entries.find(entry => entry.session.id === sessionId);
      if (selected) state$.next({
        ...state$.value,
        session: { ...selected.session, local_runtime_state: 'active' },
        role: selected.role,
      });
    }),
    endSession: vi.fn(),
    leaveSession: vi.fn(),
  };
  TestBed.configureTestingModule({
    imports: [PairSessionCatalogComponent],
    providers: [{ provide: ShareSessionService, useValue: service }],
  });
  return service;
}

describe('PairSessionCatalogComponent', () => {
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('lists the active and parked memberships and switches without retiring either one', async () => {
    vi.useFakeTimers();
    const active: ShareSessionCatalogEntry = { session: session('session-a', 'Mit Alice', 1), role: 'owner' };
    const parked: ShareSessionCatalogEntry = { session: session('session-b', 'Mit Bob', 2), role: 'participant' };
    const service = configure([active, parked]);
    const fixture = TestBed.createComponent(PairSessionCatalogComponent);
    const switched = vi.fn();
    fixture.componentInstance.switched.subscribe(switched);
    await fixture.componentInstance.load();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(service.listSessions).toHaveBeenCalledWith({ expectedAuthority: 'public' });
    expect(host.textContent).toContain('Mit Alice');
    expect(host.textContent).toContain('aktiv verbunden');
    expect(host.textContent).toContain('Mit Bob');
    expect(host.textContent).toContain('geparkt');
    expect(host.textContent).toMatch(/Peer-[a-f0-9]{6}/);
    expect(host.textContent).not.toContain('session-b-private-owner-peer');

    host.querySelector<HTMLButtonElement>('[data-testid="switch-pair-session"]')?.click();
    await Promise.resolve();

    expect(service.switchToSession).toHaveBeenCalledWith('session-b', { expectedAuthority: 'public' });
    expect(service.endSession).not.toHaveBeenCalled();
    expect(service.leaveSession).not.toHaveBeenCalled();
    expect(switched).toHaveBeenCalledWith('session-b');
  });

  it('opens the active session without reconnecting or retiring it', async () => {
    vi.useFakeTimers();
    const active: ShareSessionCatalogEntry = { session: session('session-a', 'Mit Alice', 1), role: 'owner' };
    const service = configure([active]);
    const fixture = TestBed.createComponent(PairSessionCatalogComponent);
    const opened = vi.fn();
    fixture.componentInstance.opened.subscribe(opened);
    await fixture.componentInstance.load();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const row = host.querySelector<HTMLElement>('.catalog-row.active');
    const button = host.querySelector<HTMLButtonElement>('[data-testid="open-pair-session"]');
    expect(row?.getAttribute('aria-current')).toBe('true');
    expect(button?.type).toBe('button');
    expect(button?.textContent).toContain('Chat öffnen');
    expect(button?.getAttribute('aria-label')).toContain('Mit Alice');

    button?.click();

    expect(opened).toHaveBeenCalledWith('session-a');
    expect(service.switchToSession).not.toHaveBeenCalled();
    expect(service.endSession).not.toHaveBeenCalled();
    expect(service.leaveSession).not.toHaveBeenCalled();
  });

  it('keeps a failed switch retryable and renders a local error', async () => {
    vi.useFakeTimers();
    const active: ShareSessionCatalogEntry = { session: session('session-a', 'Mit Alice', 1), role: 'owner' };
    const parked: ShareSessionCatalogEntry = { session: session('session-b', 'Mit Bob', 2), role: 'participant' };
    const service = configure([active, parked]);
    service.switchToSession.mockRejectedValueOnce(new Error('pair_session_switch_target_unavailable'));
    const fixture = TestBed.createComponent(PairSessionCatalogComponent);
    await fixture.componentInstance.load();
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="switch-pair-session"]');
    button?.click();
    await Promise.resolve();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('nicht mehr verfügbar');
    expect(button?.disabled).toBe(false);
  });

  it('offers server reactivation when the locally selected session was parked by another tab', async () => {
    vi.useFakeTimers();
    const parkedSelected: ShareSessionCatalogEntry = {
      session: {
        ...session('session-a', 'Mit Alice', 1),
        identity_binding_version: 2,
        local_runtime_state: 'parked',
      },
      role: 'owner',
    };
    const service = configure([parkedSelected]);
    const fixture = TestBed.createComponent(PairSessionCatalogComponent);
    await fixture.componentInstance.load();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('lokal ausgewählt · serverseitig geparkt');
    const button = host.querySelector<HTMLButtonElement>('[data-testid="switch-pair-session"]');
    expect(button?.textContent).toContain('Wieder aktivieren');

    button?.click();
    await Promise.resolve();
    fixture.detectChanges();

    expect(service.switchToSession).toHaveBeenCalledWith('session-a', { expectedAuthority: 'public' });
    expect(host.textContent).toContain('aktiv verbunden');
  });
});
