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
    isPopupCallback: vi.fn(() => false),
  };
  const originalOpener = Object.getOwnPropertyDescriptor(window, 'opener');

  beforeEach(() => {
    vi.clearAllMocks();
    oidc.isPopupCallback.mockReturnValue(false);
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

  it('uses the state-classified popup callback even when window.opener was lost', async () => {
    oidc.isPopupCallback.mockReturnValue(true);
    window.history.replaceState({}, '', '/oidc-callback?code=standard&state=p.popup-state');
    const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
    const component = TestBed.createComponent(OidcCallbackComponent).componentInstance;

    await component.ngOnInit();

    expect(oidc.handleCallbackForPopup).toHaveBeenCalledOnce();
    expect(oidc.handleBackendCallback).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });

  it('does not classify a normal callback as popup merely because an opener exists', async () => {
    Object.defineProperty(window, 'opener', { configurable: true, value: {} });
    window.history.replaceState({}, '', '/oidc-callback?code=standard&state=r.redirect-state');
    const component = TestBed.createComponent(OidcCallbackComponent).componentInstance;

    await component.ngOnInit();

    expect(oidc.handleCallback).toHaveBeenCalledOnce();
    expect(oidc.handleCallbackForPopup).not.toHaveBeenCalled();
  });

  it('reserves oidc_code for the Hub backend callback', async () => {
    window.history.replaceState({}, '', '/oidc-callback?oidc_code=broker-code');
    const component = TestBed.createComponent(OidcCallbackComponent).componentInstance;

    await component.ngOnInit();

    expect(oidc.handleBackendCallback).toHaveBeenCalledOnce();
    expect(oidc.handleCallback).not.toHaveBeenCalled();
  });
});
