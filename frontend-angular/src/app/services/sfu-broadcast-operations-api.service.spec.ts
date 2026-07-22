import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { HubApiCoreService } from './hub-api-core.service';
import { SfuBroadcastOperationsApiService } from './sfu-broadcast-operations-api.service';

describe('SfuBroadcastOperationsApiService', () => {
  it('reads only bounded snapshot filters and parses content-free diagnostics', async () => {
    const core = { request: vi.fn(() => of(page())) };
    TestBed.configureTestingModule({ providers: [
      SfuBroadcastOperationsApiService,
      { provide: HubApiCoreService, useValue: core },
    ] });
    const service = TestBed.inject(SfuBroadcastOperationsApiService);

    const result = await firstValueFrom(service.read('https://hub.test/', {
      tenantRef: 'tenant-a', region: 'eu-central', pageSize: 25,
    }));

    expect(result.items[0]).toMatchObject({ routeStatus: 'applied', gateState: 'observe_only' });
    expect(core.request).toHaveBeenCalledWith(
      'GET',
      expect.stringContaining('tenant_ref=tenant-a&region=eu-central&page_size=25'),
      'https://hub.test',
      { timeoutMs: 8_000 },
    );
  });

  it('sends confirmed commands once with an idempotency header and exact snake-case body', async () => {
    const core = { request: vi.fn(() => of({
      ok: true, accepted: true, effective_version: 8, state: 'active',
      reason_code: 'sfu_broadcast_preferences_updated', command_ref: 'command-a', replayed: false,
    })) };
    TestBed.configureTestingModule({ providers: [
      SfuBroadcastOperationsApiService,
      { provide: HubApiCoreService, useValue: core },
    ] });
    const service = TestBed.inject(SfuBroadcastOperationsApiService);

    await firstValueFrom(service.command('https://hub.test', {
      roomRef: 'room-a', command: 'set_preferences', expectedVersion: 7, confirmed: true,
      options: { dataSaver: true, audioOnly: false, qualityPreference: 'low' },
    }, 'sfb-command-0123456789abcdef'));

    expect(core.request).toHaveBeenCalledWith('POST', expect.stringContaining('/commands'), 'https://hub.test', {
      body: {
        room_ref: 'room-a', command: 'set_preferences', expected_version: 7, confirmed: true,
        options: { data_saver: true, audio_only: false, quality_preference: 'low' },
      },
      headers: { 'Idempotency-Key': 'sfb-command-0123456789abcdef' },
      timeoutMs: 8_000,
    });
  });

  it('rejects response expansion before exposing unknown or sensitive fields', async () => {
    const expanded = page();
    (expanded.items[0] as any).access_token = 'must-not-reach-ui';
    const core = { request: vi.fn(() => of(expanded)) };
    TestBed.configureTestingModule({ providers: [
      SfuBroadcastOperationsApiService,
      { provide: HubApiCoreService, useValue: core },
    ] });
    const service = TestBed.inject(SfuBroadcastOperationsApiService);

    await expect(firstValueFrom(service.read('https://hub.test', { tenantRef: 'tenant-a' })))
      .rejects.toThrow('sfu_operations_response_invalid');
  });
});

function page(): any {
  return {
    ok: true,
    reason_code: 'sfu_operations_snapshot_read',
    snapshot_ref: 'snapshot-a',
    next_cursor: null,
    items: [{
      room_diagnostic_ref: 'room-pseudo-a', receiver_diagnostic_ref: null,
      region_diagnostic_ref: 'region-pseudo-a', group_status: 'active', route_status: 'applied',
      epoch_class: 'current', topology: 'sfu_broadcast', health: 'healthy',
      layers: { requested: 'high', allowed: 'medium', effective: 'medium', distribution: { medium: 12 } },
      queue: { depth_bucket: 2, drop_reason: 'none' },
      traffic: { ingress_bucket: 512000, egress_bucket: 2500000, turn_bucket: 0 },
      rekey_status: 'current', failover_status: 'stable', capacity_profile: 'legacy-safe-default',
      gate_state: 'observe_only',
    }],
  };
}
