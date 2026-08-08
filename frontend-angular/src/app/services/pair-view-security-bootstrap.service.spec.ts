import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { ShareSession } from './share-session.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { canonicalSecurityJson } from './webrtc-secure-envelope';

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
        confirmation: { confirmation_tag: 'peer-tag', package_id: 'a'.repeat(64), epoch: 3 },
      })),
      post: vi.fn((_url: string, body: unknown) => { posts.push(body); return of({ ok: true }); }),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(true);
    expect(peerKeys.verifyAndBind).toHaveBeenCalledOnce();
    expect(posts).toEqual([expect.objectContaining({
      recipient_peer_id: 'bob', package_id: 'a'.repeat(64), epoch: 3,
    })]);
    expect(peerKeys.acceptPeerConfirmation).toHaveBeenCalledWith('peer-tag');
    expect(bootstrap.state$.value.status).toBe('ready');
    expect(bootstrap.confirmedRemotePeerId).toBe('bob');
  });

  it('keeps the signaling audience unavailable while waiting for the peer', async () => {
    const response = await keyPackageResponse();
    response.packages = [];
    response.security_contract = null;
    response.security_contract_digest = null;
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true })) };
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
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndBind).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({ status: 'failed', reasonCode: 'algorithm_invalid' });
  });
});

function configure(core: unknown, peerKeys: unknown): void {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    { provide: HubApiCoreService, useValue: core },
    { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
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
    verifyAndBind: vi.fn(async function (this: any) { this.currentBinding = binding; return binding; }),
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
    hub_key_id: 'hub-key',
    hub_public_key_b64: 'unused-by-mock',
    packages: [{ package_id: 'a'.repeat(64), membership_id: 'participant-a' }],
  };
}

function proposal(sender: string, recipient: string) {
  return {
    version: 1, negotiation_id: 'neg:0123456789abcdef', scope_kind: 'session', scope_id: 'session-a',
    sender_id: sender, recipient_id: recipient, minimum_mode: 'strict_e2ee', selected_mode: 'strict_e2ee',
    algorithms: ['AES-256-GCM', 'ECDH-P256-HKDF-SHA256'], key_epoch: 3,
    payload_classes: ['bulk', 'control', 'media', 'semantic'], expires_at_ms: Number.MAX_SAFE_INTEGER,
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
