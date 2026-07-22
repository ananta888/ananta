import { Injectable } from '@angular/core';

import type { SfuBroadcastJsonObject } from './sfu-broadcast-contracts';
import type { SfuRemoteVideoHandle, SfuStatsPort, SfuStatsSnapshot } from './sfu-room-session.ports';

export interface SfuBroadcastQualitySample extends SfuBroadcastJsonObject {
  readonly sample_sequence: number;
  readonly observed_at: string;
  readonly window_ms: number;
  readonly metrics: SfuBroadcastJsonObject;
}

interface PreviousSnapshot {
  readonly sequence: number;
  readonly value: SfuStatsSnapshot;
}

@Injectable({ providedIn: 'root' })
export class SfuBroadcastQualitySamplerService {
  private readonly previous = new Map<string, PreviousSnapshot>();

  async sample(
    port: SfuStatsPort,
    handle: SfuRemoteVideoHandle,
  ): Promise<SfuBroadcastQualitySample | null> {
    if (port.capability !== 'available' || !port.read) return null;
    const snapshot = await port.read(handle);
    if (!snapshot) return null;
    const prior = this.previous.get(handle.handleId);
    const sequence = (prior?.sequence ?? 0) + 1;
    const windowMs = prior
      ? Math.max(100, Math.min(2_000, Math.trunc(snapshot.observedAtMs - prior.value.observedAtMs)))
      : 1_000;
    const reset = Boolean(prior && countersReset(snapshot, prior.value));
    const metrics: Record<string, number> = {};
    add(metrics, 'rtt_ms', bucket(snapshot.roundTripTimeSeconds, 1_000, 60_000));
    add(metrics, 'jitter_ms', bucket(snapshot.jitterSeconds, 1_000, 10_000));
    if (prior && !reset) {
      const packets = snapshot.packetsReceived - prior.value.packetsReceived;
      const lost = snapshot.packetsLost - prior.value.packetsLost;
      if (packets + lost > 0) {
        metrics['packet_loss_basis_points'] = clampInt(lost * 10_000 / (packets + lost), 0, 10_000);
      }
      metrics['receive_bitrate_bps'] = clampInt(
        (snapshot.bytesReceived - prior.value.bytesReceived) * 8_000 / windowMs,
        0,
        1_000_000_000,
      );
      const frames = snapshot.framesDecoded - prior.value.framesDecoded;
      const decodeSeconds = snapshot.totalDecodeTimeSeconds - prior.value.totalDecodeTimeSeconds;
      if (frames > 0) metrics['decode_time_ms_per_frame'] = clampInt(decodeSeconds * 1_000 / frames, 0, 1_000);
      metrics['freeze_duration_ms'] = clampInt(
        (snapshot.totalFreezeDurationSeconds - prior.value.totalFreezeDurationSeconds) * 1_000,
        0,
        2_000,
      );
    }
    this.previous.set(handle.handleId, { sequence, value: snapshot });
    if (!Object.keys(metrics).length) return null;
    return Object.freeze({
      sample_sequence: sequence,
      observed_at: utcSecond(snapshot.observedAtMs),
      window_ms: windowMs,
      metrics: Object.freeze(metrics),
    });
  }

  reset(handleId?: string): void {
    if (handleId) this.previous.delete(handleId);
    else this.previous.clear();
  }
}

function countersReset(next: SfuStatsSnapshot, previous: SfuStatsSnapshot): boolean {
  return next.bytesReceived < previous.bytesReceived
    || next.packetsReceived < previous.packetsReceived
    || next.packetsLost < previous.packetsLost
    || next.framesDecoded < previous.framesDecoded
    || next.totalDecodeTimeSeconds < previous.totalDecodeTimeSeconds
    || next.totalFreezeDurationSeconds < previous.totalFreezeDurationSeconds;
}

function add(target: Record<string, number>, key: string, value: number | null): void {
  if (value !== null) target[key] = value;
}

function bucket(value: number | null, factor: number, max: number): number | null {
  return value === null ? null : clampInt(value * factor, 0, max);
}

function clampInt(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value)) return minimum;
  return Math.max(minimum, Math.min(maximum, Math.round(value)));
}

function utcSecond(value: number): string {
  return new Date(Math.floor(value / 1_000) * 1_000).toISOString().replace('.000Z', 'Z');
}
