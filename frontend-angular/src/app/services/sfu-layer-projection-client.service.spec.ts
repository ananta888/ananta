import { TestBed } from '@angular/core/testing';
import { NEVER, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { SfuBroadcastReceiverLayerControllerService } from './sfu-broadcast-receiver-layer-controller.service';
import { SfuLayerProjectionApiService } from './sfu-layer-projection-api.service';
import { SfuLayerProjectionClientService } from './sfu-layer-projection-client.service';
import { SfuLayerProjectionValidator } from './sfu-layer-projection.validator';
import type { SfuSubscriptionLayerPort } from './sfu-room-session.ports';

const binding = {
  tenantRef: 'tenant-a', roomRef: 'room-a', receiverRef: 'receiver-a',
  subscriptionRef: 'sub-a', publicationRef: 'pub-a', membershipEpoch: 2,
  routeEpoch: 3, topologyEpoch: 4, keyEpoch: 5, sessionProjectionVersion: 1,
};
const port: SfuSubscriptionLayerPort = {
  layerControlMode: 'manual_quality', applyRemoteSubscriptions: () => undefined,
  setRemotePublicationSubscribed: () => undefined,
};
const projection = {
  brand: 'validated-layer-projection', kind: 'receiver', scopeKey: 'scope-a',
  version: 1, fencingToken: 1,
  contract: { contractId: 'ananta.sfu-receiver-layer-projection.v1', document: {} },
} as const;
const response = {
  status: 'projection', cursor: 1, etag: 'etag-a', document: {}, rawDocument: '{}',
  digest: 'a'.repeat(64), signature: 'signature', signatureKeyId: 'key-a',
} as const;

describe('SfuLayerProjectionClientService', () => {
  it('returns a cancellation handle without waiting for a hanging initial read', async () => {
    const api = { read: vi.fn(() => NEVER) };
    const receivers = { stop: vi.fn(), fallback: vi.fn(), bindAndApply: vi.fn() };
    TestBed.configureTestingModule({ providers: [
      SfuLayerProjectionClientService,
      { provide: SfuLayerProjectionApiService, useValue: api },
      { provide: SfuLayerProjectionValidator, useValue: { reset: vi.fn() } },
      { provide: SfuBroadcastReceiverLayerControllerService, useValue: receivers },
    ] });
    const service = TestBed.inject(SfuLayerProjectionClientService);

    const release = await service.bindReceiver(binding, port);
    expect(typeof release).toBe('function');
    release();
    expect(receivers.stop).toHaveBeenCalledWith('sub-a');
  });

  it('does not commit projection state when controller apply fails', async () => {
    const validator = {
      validateAsync: vi.fn(async () => ({ valid: true, reasonCode: 'ok', projection })),
      commit: vi.fn(), reset: vi.fn(),
    };
    const receivers = {
      stop: vi.fn(), fallback: vi.fn(), bindAndApply: vi.fn(async () => false),
    };
    TestBed.configureTestingModule({ providers: [
      SfuLayerProjectionClientService,
      { provide: SfuLayerProjectionApiService, useValue: { read: vi.fn(() => of(response)) } },
      { provide: SfuLayerProjectionValidator, useValue: validator },
      { provide: SfuBroadcastReceiverLayerControllerService, useValue: receivers },
    ] });
    const service = TestBed.inject(SfuLayerProjectionClientService);
    const release = await service.bindReceiver(binding, port);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(receivers.bindAndApply).toHaveBeenCalledOnce();
    expect(validator.commit).not.toHaveBeenCalled();
    release();
  });
});
