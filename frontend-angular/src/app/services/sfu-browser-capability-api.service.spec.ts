import { HttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { SfuBrowserCapabilityApiService } from './sfu-browser-capability-api.service';

describe('SfuBrowserCapabilityApiService', () => {
  it('rejects cross-room observations before issuing an HTTP request', async () => {
    const http = { post: vi.fn() };
    TestBed.configureTestingModule({ providers: [
      SfuBrowserCapabilityApiService,
      { provide: HttpClient, useValue: http },
    ] });
    const service = TestBed.inject(SfuBrowserCapabilityApiService);

    await expect(service.submit('room-a', {
      room_ref: 'room-b', tenant_ref: 'tenant-a', browser_instance_pseudonym: 'room-bip_0123456789012345678901', sequence: 1,
    } as never)).rejects.toThrow('sfu_capability_room_scope_mismatch');
    expect(http.post).not.toHaveBeenCalled();
  });

  it('bounds per-browser optimistic-version state', async () => {
    const http = { post: vi.fn((_url: string, body: string) => {
      const observation = JSON.parse(body) as { sequence: number };
      return of({
        ok: true, state: 'active', capability_class: 'baseline', version: 1,
        sequence: observation.sequence, reevaluation_required: false,
      });
    }) };
    TestBed.configureTestingModule({ providers: [
      SfuBrowserCapabilityApiService,
      { provide: HttpClient, useValue: http },
    ] });
    const service = TestBed.inject(SfuBrowserCapabilityApiService);
    for (let index = 0; index < 257; index += 1) {
      await service.submit('room-a', {
        room_ref: 'room-a', tenant_ref: 'tenant-a',
        browser_instance_pseudonym: `browser-${index}`, sequence: index + 1,
      } as never);
    }

    expect((service as any).versions.size).toBe(256);
  });
});
