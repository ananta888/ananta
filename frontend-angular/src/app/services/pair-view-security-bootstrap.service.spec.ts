import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { ShareSession } from './share-session.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { canonicalSecurityJson } from './webrtc-secure-envelope';
import {
  PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
  PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64,
} from './public-ananta-endpoints';

const session: ShareSession = {
  id: 'session-a', title: 'Strict Pair', invite_code: 'invite', mode: 'p2p', transport: 'webrtc',
  permissions: { chat: true }, created_at: 1, expires_at: null, revoked_at: null,
  owner_user_id: 'alice', security_epoch: 3, security_contract_version: 1, security_mode: 'strict_e2ee',
};

describe('PairViewSecurityBootstrapService production contract seam', () => {
  it('validates the final transcript before binding and confirming the peer', async () => {
    const response = await keyPackageResponse();
    const peerKeys = fakePeerKeys();
    const posts: unknown[] = [];
    const core = {
      get: vi.fn((url: string) => of(url.includes('key-packages') ? response : {
        ok: true,
        local_peer_id: 'alice',
        confirmation: confirmation(),
      })),
      post: vi.fn((_url: string, body: unknown) => {
        posts.push(body);
        return of({ ok: true, local_peer_id: 'alice' });
      }),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(true);
    expect(peerKeys.verifyAndRefreshBinding).toHaveBeenCalledOnce();
    expect(posts).toEqual([expect.objectContaining({
      recipient_peer_id: 'bob', package_id: 'a'.repeat(64), epoch: 3,
    })]);
    expect(peerKeys.acceptPeerConfirmation).toHaveBeenCalledWith(confirmationTag());
    expect(bootstrap.state$.value.status).toBe('ready');
    expect(bootstrap.confirmedRemotePeerId).toBe('bob');
  });

  it('keeps the signaling audience unavailable while waiting for the peer', async () => {
    const response = await keyPackageResponse();
    response.packages = [];
    response.security_contract = null;
    response.security_contract_digest = null;
    response.local_package_id = null;
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(bootstrap.state$.value.status).toBe('waiting_for_peer');
    expect(bootstrap.confirmedRemotePeerId).toBe('');
    expect(core.post).not.toHaveBeenCalled();
  });

  it('fails closed before peer binding when the negotiated algorithm is mutated', async () => {
    const response = await keyPackageResponse();
    response.security_contract.offer.algorithms = ['AES-256-GCM'];
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({ status: 'failed', reasonCode: 'algorithm_invalid' });
  });

  it('rejects a non-self-certifying rendezvous key id for a public session', async () => {
    const response = await keyPackageResponse();
    response.packages = [];
    response.security_contract = null;
    response.security_contract_digest = null;
    response.local_package_id = null;
    response.hub_key_id = 'attacker-controlled-key-id';
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({
      status: 'failed', reasonCode: 'public_hub_key_id_invalid',
    });
  });

  it('rejects a self-consistent but unpinned alternative rendezvous authority', async () => {
    const response = await keyPackageResponse();
    const alternativeKey = String.fromCharCode(...new Uint8Array(32).fill(7));
    response.hub_public_key_b64 = btoa(alternativeKey);
    response.hub_key_id = `rv:${(await sha256(alternativeKey)).slice(0, 24)}`;
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({
      status: 'failed', reasonCode: 'public_hub_authority_untrusted',
    });
  });

  it('rejects a confirmation for another package before accepting its tag', async () => {
    const response = await keyPackageResponse();
    const peerKeys = fakePeerKeys();
    const core = {
      get: vi.fn((url: string) => of(url.includes('key-packages') ? response : {
        ok: true, local_peer_id: 'alice',
        confirmation: { ...confirmation(), package_id: 'c'.repeat(64) },
      })),
      post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.acceptPeerConfirmation).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({
      status: 'failed', reasonCode: 'key_confirmation_package_mismatch',
    });
  });

  it('rejects an expired confirmation before accepting its tag', async () => {
    const response = await keyPackageResponse();
    const peerKeys = fakePeerKeys();
    const core = {
      get: vi.fn((url: string) => of(url.includes('key-packages') ? response : {
        ok: true,
        local_peer_id: 'alice',
        confirmation: {
          ...confirmation(), created_at_ms: Date.now() - 600_000, expires_at_ms: Date.now() - 300_000,
        },
      })),
      post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.acceptPeerConfirmation).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({ status: 'failed', reasonCode: 'key_confirmation_stale' });
  });
});

function configure(core: any, peerKeys: unknown): void {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    { provide: HubApiCoreService, useValue: core },
    { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
    { provide: PairSessionControlPlaneService, useValue: {
      securityGet: (_sessionId: string, suffix: string) => core.get(suffix),
      securityPost: (_sessionId: string, suffix: string, body: unknown) => core.post(suffix, body),
      isPublicSession: () => true,
    } },
    { provide: WebrtcPeerKeyService, useValue: peerKeys },
  ] });
}

function fakePeerKeys() {
  const binding: any = {
    packageId: 'a'.repeat(64), scopeId: 'session-a', epoch: 3,
    remotePeerId: 'bob', peerFingerprint: 'f'.repeat(64), confirmed: false,
    fingerprintChanged: false,
  };
  return {
    currentBinding: null as any,
    clear: vi.fn(), approveFingerprintChange: vi.fn(),
    verifyAndRefreshBinding: vi.fn(async function (this: any) { this.currentBinding = binding; return binding; }),
    createConfirmation: vi.fn(async () => 'local-tag'),
    acceptPeerConfirmation: vi.fn(async function (this: any) { binding.confirmed = true; }),
  };
}

async function keyPackageResponse(): Promise<any> {
  const offer: any = proposal('owner-session-a', 'participant-a');
  const answer: any = proposal('participant-a', 'owner-session-a');
  const digest = await sha256(canonicalSecurityJson({
    domain: 'ananta.webrtc.security-negotiation.v1', offer, answer,
  }));
  return {
    ok: true,
    epoch: 3,
    tenant_id: 'tenant-a',
    security_contract_digest: digest,
    security_contract: {
      version: 1, negotiation_id: offer.negotiation_id, offer, answer, digest,
      signature: 'b'.repeat(64), signature_algorithm: 'HMAC-SHA256',
    },
    hub_key_id: PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
    hub_public_key_b64: PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64,
    local_membership_id: 'owner-session-a',
    local_peer_id: 'alice',
    local_package_id: 'b'.repeat(64),
    packages: [{ package_id: 'a'.repeat(64), membership_id: 'participant-a' }],
  };
}

function confirmation() {
  const now = Date.now();
  return {
    confirmation_tag: confirmationTag(), package_id: 'b'.repeat(64), epoch: 3,
    created_at_ms: now - 1_000, expires_at_ms: now + 240_000,
  };
}

function confirmationTag(): string {
  return btoa('t'.repeat(32));
}

function proposal(sender: string, recipient: string) {
  return {
    version: 1, negotiation_id: 'neg:0123456789abcdef', scope_kind: 'session', scope_id: 'session-a',
    sender_id: sender, recipient_id: recipient, minimum_mode: 'strict_e2ee', selected_mode: 'strict_e2ee',
    algorithms: ['AES-256-GCM', 'ECDH-P256-HKDF-SHA256'], key_epoch: 3,
    payload_classes: ['bulk', 'control', 'semantic'], expires_at_ms: Number.MAX_SAFE_INTEGER,
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
