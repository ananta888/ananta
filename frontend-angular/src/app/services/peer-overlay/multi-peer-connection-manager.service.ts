import { Injectable, inject } from '@angular/core';

import {
  PeerLinkSession,
  PEER_LINK_SESSION_FACTORY,
  PeerMediaSlot,
  PeerMediaTrackHandle,
  ValidatedPeerLinkTicket,
} from './peer-media-fanout.ports';

export interface PeerConnectionManagerSnapshot {
  readonly peerIds: readonly string[];
  readonly failedPeerIds: readonly string[];
  readonly connectionCount: number;
}

/**
 * Owns independent peer sessions for a small mesh. Signaling and global route
 * authority remain with the Hub; one failed link never closes a sibling.
 */
@Injectable({ providedIn: 'root' })
export class MultiPeerConnectionManager {
  private readonly factory = inject(PEER_LINK_SESSION_FACTORY);
  private readonly links = new Map<string, PeerLinkSession>();
  private readonly failed = new Set<string>();
  private readonly tracks = new Map<PeerMediaSlot, PeerMediaTrackHandle | null>();
  private readonly muted = new Map<PeerMediaSlot, boolean>();
  private revision = 0;

  async reconcile(tickets: readonly ValidatedPeerLinkTicket[]): Promise<PeerConnectionManagerSnapshot> {
    const operation = ++this.revision;
    const unique = new Map(tickets.map(ticket => [ticket.remotePeerId, ticket]));
    if (unique.size !== tickets.length || unique.size > 3) throw new Error('peer_mesh_size_invalid');
    const desired = new Set(unique.keys());
    for (const [peerId, session] of this.links) {
      if (desired.has(peerId)) continue;
      session.lifecycle.close();
      this.links.delete(peerId);
      this.failed.delete(peerId);
    }
    await Promise.all([...unique].map(async ([peerId, ticket]) => {
      if (this.links.has(peerId)) return;
      try {
        const session = await this.factory.create(ticket);
        if (operation !== this.revision || !desired.has(peerId)) {
          session.lifecycle.close();
          return;
        }
        await this.applyLocalState(session);
        this.links.set(peerId, session);
        this.failed.delete(peerId);
      } catch {
        this.failed.add(peerId);
      }
    }));
    return this.snapshot();
  }

  async setTrack(slot: PeerMediaSlot, track: PeerMediaTrackHandle | null): Promise<void> {
    this.tracks.set(slot, track);
    await this.forEachIsolated(session => session.publications.setTrack(slot, track));
  }

  async setMuted(slot: PeerMediaSlot, muted: boolean): Promise<void> {
    this.muted.set(slot, muted);
    await this.forEachIsolated(session => session.publications.setMuted(slot, muted));
  }

  restartPeer(peerId: string): void {
    const session = this.links.get(peerId);
    if (!session) throw new Error('peer_link_not_found');
    try { session.lifecycle.restartIce(); } catch { this.failed.add(peerId); }
  }

  disconnectPeer(peerId: string): void {
    const session = this.links.get(peerId);
    if (!session) return;
    session.lifecycle.close();
    this.links.delete(peerId);
    this.failed.delete(peerId);
  }

  close(): void {
    this.revision += 1;
    for (const session of this.links.values()) session.lifecycle.close();
    this.links.clear();
    this.failed.clear();
  }

  snapshot(): PeerConnectionManagerSnapshot {
    return Object.freeze({
      peerIds: Object.freeze([...this.links.keys()].sort()),
      failedPeerIds: Object.freeze([...this.failed].sort()),
      connectionCount: this.links.size,
    });
  }

  private async applyLocalState(session: PeerLinkSession): Promise<void> {
    for (const [slot, track] of this.tracks) await session.publications.setTrack(slot, track);
    for (const [slot, muted] of this.muted) await session.publications.setMuted(slot, muted);
  }

  private async forEachIsolated(operation: (session: PeerLinkSession) => Promise<void>): Promise<void> {
    await Promise.all([...this.links].map(async ([peerId, session]) => {
      try { await operation(session); } catch { this.failed.add(peerId); }
    }));
  }
}
