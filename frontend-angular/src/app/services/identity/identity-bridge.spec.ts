import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IdentityBridge } from './identity-bridge';
import { NetworkProfileService } from '../network-profile.service';
import { AgentDirectoryService } from '../agent-directory.service';

describe('IdentityBridge', () => {
  let bridge: IdentityBridge;

  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  function build(profileId: string, hubUrl: string | null) {
    TestBed.configureTestingModule({
      providers: [
        IdentityBridge,
        {
          provide: NetworkProfileService,
          useValue: {
            current: { profile_id: profileId },
          },
        },
        {
          provide: AgentDirectoryService,
          useValue: {
            list: () => (hubUrl ? [{ role: 'hub', url: hubUrl }] : []),
          },
        },
      ],
    });
    bridge = TestBed.inject(IdentityBridge);
  }

  describe('findApplicableRules', () => {
    it('returns the public-ananta rule when profile matches and hub is present', () => {
      build('public-ananta', 'http://hub.test');
      const rules = bridge.findApplicableRules('oidc');
      expect(rules).toHaveLength(1);
      expect(rules[0].id).toBe('public-ananta.oidc-to-hub');
    });

    it('returns no rules for non-public-ananta profile', () => {
      build('local', 'http://hub.test');
      const rules = bridge.findApplicableRules('oidc');
      expect(rules).toHaveLength(0);
    });

    it('returns no rules when no hub is in directory', () => {
      build('public-ananta', null);
      const rules = bridge.findApplicableRules('oidc');
      expect(rules).toHaveLength(0);
    });

    it('returns no rules for from=hub (no hub→something rules today)', () => {
      build('public-ananta', 'http://hub.test');
      const rules = bridge.findApplicableRules('hub');
      expect(rules).toHaveLength(0);
    });
  });

  describe('buildContext', () => {
    it('exposes activeProfile from NetworkProfileService', () => {
      build('public-ananta', 'http://hub.test');
      const ctx = bridge.buildContext();
      expect(ctx.activeProfile).toBe('public-ananta');
    });

    it('exposes hubUrl() from AgentDirectoryService', () => {
      build('public-ananta', 'http://hub.test:8080');
      const ctx = bridge.buildContext();
      expect(ctx.hubUrl()).toBe('http://hub.test:8080');
    });

    it('returns empty hubUrl when no hub', () => {
      build('public-ananta', null);
      const ctx = bridge.buildContext();
      expect(ctx.hubUrl()).toBe('');
    });
  });

  describe('mode / showOidcLogin / showHubDirectLogin', () => {
    it('public-ananta with hub → oidc-bridge', () => {
      build('public-ananta', 'http://hub.test');
      expect(bridge.mode()).toBe('oidc-bridge');
      expect(bridge.showOidcLogin).toBe(true);
      expect(bridge.showHubDirectLogin).toBe(false);
    });

    it('local profile → hub-direct', () => {
      build('local', 'http://hub.test');
      expect(bridge.mode()).toBe('hub-direct');
      expect(bridge.showOidcLogin).toBe(false);
      expect(bridge.showHubDirectLogin).toBe(true);
    });

    it('public-ananta without hub → hub-direct (fallback)', () => {
      build('public-ananta', null);
      expect(bridge.mode()).toBe('hub-direct');
      expect(bridge.showOidcLogin).toBe(false);
      expect(bridge.showHubDirectLogin).toBe(true);
    });
  });
});