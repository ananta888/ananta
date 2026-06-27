import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Router } from '@angular/router';

import { AuthRequiredRouter } from './auth-required-router.service';
import { AuthRefreshCoordinator } from './auth-refresh-coordinator.service';
import { BehaviorSubject } from 'rxjs';

describe('AuthRequiredRouter — Welle 6 redirect on authRequired', () => {
  let router: { navigate: ReturnType<typeof vi.fn> };
  let coordinator: { authRequired$: BehaviorSubject<'hub' | 'oidc' | null> };

  beforeEach(() => {
    TestBed.resetTestingModule();
    router = { navigate: vi.fn() };
    coordinator = { authRequired$: new BehaviorSubject<'hub' | 'oidc' | null>(null) };
    TestBed.configureTestingModule({
      providers: [
        AuthRequiredRouter,
        { provide: Router, useValue: router },
        { provide: AuthRefreshCoordinator, useValue: coordinator },
      ],
    });
  });

  it('navigates to /login with sphere=oidc when bridge-active refresh fails', () => {
    const svc = TestBed.inject(AuthRequiredRouter);
    svc.start();
    coordinator.authRequired$.next('oidc');
    expect(router.navigate).toHaveBeenCalledWith(['/login'], { queryParams: { sphere: 'oidc' } });
  });

  it('navigates to /login with sphere=hub when hub-direct refresh fails', () => {
    const svc = TestBed.inject(AuthRequiredRouter);
    svc.start();
    coordinator.authRequired$.next('hub');
    expect(router.navigate).toHaveBeenCalledWith(['/login'], { queryParams: { sphere: 'hub' } });
  });

  it('does NOT navigate on the initial null emission', () => {
    const svc = TestBed.inject(AuthRequiredRouter);
    svc.start();
    expect(router.navigate).not.toHaveBeenCalled();
  });
});