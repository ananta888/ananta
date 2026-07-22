import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import type { BrowserMediaCapabilityObservationContract } from './sfu-broadcast-contracts';

export interface SfuBrowserCapabilityUploadResult {
  readonly state: 'active' | 'unknown' | 'unsupported' | 'stale';
  readonly capabilityClass: 'advanced' | 'baseline' | 'unsupported' | 'unknown';
  readonly version: number;
  readonly sequence: number;
  readonly reevaluationRequired: boolean;
}

@Injectable({ providedIn: 'root' })
export class SfuBrowserCapabilityApiService {
  private readonly http = inject(HttpClient);
  private readonly versions = new Map<string, number>();
  private readonly versionsMax = 256;

  async submit(roomRef: string, observation: BrowserMediaCapabilityObservationContract): Promise<SfuBrowserCapabilityUploadResult> {
    if (observation.room_ref !== roomRef) throw new Error('sfu_capability_room_scope_mismatch');
    const encoded = JSON.stringify(observation);
    if (new TextEncoder().encode(encoded).byteLength > 2048) throw new Error('sfu_capability_report_bytes_exceeded');
    const key = `${observation.tenant_ref}\u0000${roomRef}\u0000${observation.browser_instance_pseudonym}`;
    const expectedVersion = this.versions.get(key) ?? 0;
    const raw = await firstValueFrom(this.http.post<Record<string, unknown>>(
      `/v1/semantic-media/sfu/rooms/${encodeURIComponent(roomRef)}/browser-capabilities`,
      encoded,
      { headers: new HttpHeaders({ 'Content-Type': 'application/json', 'If-Match': `"${expectedVersion}"` }) },
    ));
    if (raw['ok'] !== true || !Number.isSafeInteger(raw['version']) || Number(raw['version']) < 1
        || !Number.isSafeInteger(raw['sequence']) || Number(raw['sequence']) !== observation.sequence) {
      throw new Error('sfu_capability_response_invalid');
    }
    const state = String(raw['state']);
    const capabilityClass = String(raw['capability_class']);
    if (!['active', 'unknown', 'unsupported', 'stale'].includes(state)
        || !['advanced', 'baseline', 'unsupported', 'unknown'].includes(capabilityClass)) {
      throw new Error('sfu_capability_response_invalid');
    }
    this.rememberVersion(key, Number(raw['version']));
    return Object.freeze({
      state: state as SfuBrowserCapabilityUploadResult['state'],
      capabilityClass: capabilityClass as SfuBrowserCapabilityUploadResult['capabilityClass'],
      version: Number(raw['version']), sequence: Number(raw['sequence']),
      reevaluationRequired: raw['reevaluation_required'] === true,
    });
  }

  private rememberVersion(key: string, version: number): void {
    this.versions.delete(key);
    this.versions.set(key, version);
    while (this.versions.size > this.versionsMax) {
      const oldest = this.versions.keys().next().value;
      if (oldest === undefined) break;
      this.versions.delete(oldest);
    }
  }
}
