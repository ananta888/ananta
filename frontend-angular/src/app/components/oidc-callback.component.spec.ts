import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OidcAuthService } from '../services/oidc-auth.service';
import { OidcCallbackComponent } from './oidc-callback.component';

describe('OidcCallbackComponent', () => {
  const oidc = {
    handleBackendCallback: vi.fn(async () => true),
    handleCallback: vi.fn(async () => true),
    handleCallbackForPopup: vi.fn(async () => true),
  };
  const originalOpener = Object.getOwnPropertyDescriptor(window, 'opener');

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/oidc-callback');
    Object.defineProperty(window, 'opener', { configurable: true, value: null });
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [OidcCallbackComponent],
      providers: [
        { provide: OidcAuthService, useValue: oidc },
        { provide: Router, useValue: { navigate: vi.fn() } },
      ],
    });
  });

  afterEach(() => {
    if (originalOpener) {
      Object.defineProperty(window, 'opener', originalOpener);
    }
  });

  it('routes a standard authorization code through the PKCE callback', async () => {
    window.history.replaceState({}, '', '/oidc-callback?code=standard&state=s');
    const component = TestBed.createComponent(OidcCallbackComponent).componentInstance;

    await component.ngOnInit();

    expect(oidc.handleCallback).toHaveBeenCalledOnce();
    expect(oidc.handleBackendCallback).not.toHaveBeenCalled();
  });

  it('uses the popup callback and closes the popup for a standard code', async () => {
    Object.defineProperty(window, 'opener', { configurable: true, value: {} });
    window.history.replaceState({}, '', '/oidc-callback?code=standard&state=s');
    const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
    const component = TestBed.createComponent(OidcCallbackComponent).componentInstance;

    await component.ngOnInit();

    expect(oidc.handleCallbackForPopup).toHaveBeenCalledOnce();
    expect(oidc.handleBackendCallback).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });

  it('reserves oidc_code for the Hub backend callback', async () => {
    window.history.replaceState({}, '', '/oidc-callback?oidc_code=broker-code');
    const component = TestBed.createComponent(OidcCallbackComponent).componentInstance;

    await component.ngOnInit();

    expect(oidc.handleBackendCallback).toHaveBeenCalledOnce();
    expect(oidc.handleCallback).not.toHaveBeenCalled();
  });
});
