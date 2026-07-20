import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

export type OrdinaryMediaHealthStatus = 'healthy' | 'degraded' | 'disconnected' | 'unknown';

export interface OrdinaryMediaHealthWindow {
  readonly session_id: string;
  readonly peer_id: string;
  readonly window_start_ms: number;
  readonly window_end_ms: number;
  readonly window_ms: number;
  readonly connection: RTCPeerConnectionState;
  readonly track_flowing: boolean;
  readonly rtt_ms: number | null;
  readonly jitter_ms: number | null;
  readonly packet_loss_ratio: number | null;
  readonly bitrate_bps: number | null;
  readonly decoded_frames: number;
  readonly freeze_count: number;
  readonly audio_gap_count: number;
  readonly status: OrdinaryMediaHealthStatus;
  readonly reason_code: string;
}

interface Counters {
  atMs: number; bytes: number; packets: number; lost: number; decoded: number;
  freezes: number; audioGaps: number; trackIds: string;
}

@Injectable({ providedIn: 'root' })
export class WebrtcMediaHealthService {
  private readonly history = new Map<string, OrdinaryMediaHealthWindow[]>();
  private readonly previous = new Map<string, Counters>();
  private readonly healthyStreak = new Map<string, number>();
  readonly window$ = new Subject<OrdinaryMediaHealthWindow>();

  private readonly maxHistoryPerPeer = 60;

  ingest(
    sessionId: string,
    peerId: string,
    connection: RTCPeerConnectionState,
    stats: RTCStatsReport | readonly RTCStats[],
    windowStartMs: number,
    windowEndMs: number,
  ): OrdinaryMediaHealthWindow {
    if (!sessionId || !peerId || !Number.isSafeInteger(windowStartMs) || !Number.isSafeInteger(windowEndMs)
        || windowEndMs <= windowStartMs) throw new Error('ordinary_health_context_invalid');
    const rows: RTCStats[] = [];
    if (Array.isArray(stats)) rows.push(...stats);
    else stats.forEach(value => rows.push(value));
    const inbound = rows.filter(row => row.type === 'inbound-rtp' && (row as any).isRemote !== true);
    const pairs = rows.filter(row => row.type === 'candidate-pair' && ['succeeded', 'in-progress'].includes(String((row as any).state)));
    const finiteSum = (field: string): number => inbound.reduce((sum, row) => sum + finite((row as any)[field]), 0);
    const bytes = finiteSum('bytesReceived');
    const packets = finiteSum('packetsReceived');
    const lost = finiteSum('packetsLost');
    const decoded = finiteSum('framesDecoded');
    const freezes = finiteSum('freezeCount');
    const audioGaps = finiteSum('concealmentEvents') + finiteSum('silentConcealedSamples');
    const trackIds = inbound.map(row => String((row as any).trackIdentifier ?? row.id ?? '')).sort().join('|');
    const counters: Counters = { atMs: windowEndMs, bytes, packets, lost, decoded, freezes, audioGaps, trackIds };
    const key = `${sessionId}\x1f${peerId}`;
    const prior = this.previous.get(key);
    const reset = Boolean(prior && (
      bytes < prior.bytes || packets < prior.packets || lost < prior.lost || decoded < prior.decoded
      || freezes < prior.freezes || audioGaps < prior.audioGaps
    ));
    const trackChanged = Boolean(prior && prior.trackIds !== trackIds);
    const deltaMs = prior ? windowEndMs - prior.atMs : 0;
    const deltaPackets = prior && !reset && !trackChanged ? packets - prior.packets : 0;
    const deltaLost = prior && !reset && !trackChanged ? lost - prior.lost : 0;
    const bitrate = prior && deltaMs > 0 && !reset && !trackChanged ? (bytes - prior.bytes) * 8_000 / deltaMs : null;
    const flowing = Boolean(prior && !reset && !trackChanged && (deltaPackets > 0 || decoded > prior.decoded));
    const rttSeconds = pairs.map(row => finiteOrNull((row as any).currentRoundTripTime)).find(value => value !== null) ?? null;
    const jitterSeconds = inbound.map(row => finiteOrNull((row as any).jitter)).filter(value => value !== null) as number[];
    const rttMs = rttSeconds === null ? null : rttSeconds * 1000;
    const jitterMs = jitterSeconds.length ? Math.max(...jitterSeconds) * 1000 : null;
    const loss = prior && deltaPackets + deltaLost > 0 && !reset && !trackChanged
      ? Math.max(0, deltaLost) / (Math.max(0, deltaPackets) + Math.max(0, deltaLost)) : null;
    let status: OrdinaryMediaHealthStatus = 'unknown';
    let reason = prior ? 'ordinary_stats_incomplete' : 'ordinary_stats_warmup';
    if (connection === 'failed' || connection === 'disconnected' || connection === 'closed') {
      status = 'disconnected'; reason = 'ordinary_connection_lost';
    } else if (reset) {
      status = 'unknown'; reason = 'ordinary_stats_reset';
    } else if (trackChanged) {
      status = 'unknown'; reason = 'ordinary_track_changed';
    } else if (connection === 'connected' && flowing && rttMs !== null && rttMs <= 500
        && (jitterMs === null || jitterMs <= 100) && (loss === null || loss <= 0.1)
        && (!prior || freezes - prior.freezes <= 1) && (!prior || audioGaps - prior.audioGaps <= 2)) {
      status = 'healthy'; reason = 'ordinary_media_healthy';
    } else if (connection === 'connected') {
      status = 'degraded'; reason = 'ordinary_media_degraded';
    }
    const window: OrdinaryMediaHealthWindow = Object.freeze({
      session_id: sessionId, peer_id: peerId, window_start_ms: windowStartMs, window_end_ms: windowEndMs,
      window_ms: windowEndMs - windowStartMs, connection, track_flowing: flowing,
      rtt_ms: bounded(rttMs), jitter_ms: bounded(jitterMs), packet_loss_ratio: bounded(loss),
      bitrate_bps: bounded(bitrate), decoded_frames: Math.max(0, Math.trunc(decoded)),
      freeze_count: Math.max(0, Math.trunc(freezes)), audio_gap_count: Math.max(0, Math.trunc(audioGaps)),
      status, reason_code: reason,
    });
    this.previous.set(key, counters);
    const values = this.history.get(key) ?? [];
    values.push(window);
    if (values.length > this.maxHistoryPerPeer) values.splice(0, values.length - this.maxHistoryPerPeer);
    this.history.set(key, values);
    this.healthyStreak.set(key, status === 'healthy' ? (this.healthyStreak.get(key) ?? 0) + 1 : 0);
    this.window$.next(window);
    return window;
  }

  ordinaryFallbackReady(sessionId: string, peerId: string): boolean {
    return (this.healthyStreak.get(`${sessionId}\x1f${peerId}`) ?? 0) >= 3;
  }

  exportForHub(sessionId: string, peerId: string, limit = 10): readonly OrdinaryMediaHealthWindow[] {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 20) return Object.freeze([]);
    const values = this.history.get(`${sessionId}\x1f${peerId}`) ?? [];
    // Shape intentionally excludes candidate addresses, device IDs and media.
    return Object.freeze(values.slice(-limit));
  }

  reset(sessionId: string, peerId: string): void {
    const key = `${sessionId}\x1f${peerId}`;
    this.previous.delete(key); this.history.delete(key); this.healthyStreak.delete(key);
  }
}

function finite(value: unknown): number { return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0; }
function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}
function bounded(value: number | null): number | null {
  return value === null || !Number.isFinite(value) || value < 0 || value > Number.MAX_SAFE_INTEGER ? null : value;
}
