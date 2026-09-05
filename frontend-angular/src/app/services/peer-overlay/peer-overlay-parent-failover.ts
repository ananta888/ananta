export interface AcceptedPeerFailoverCommand {
  readonly validation: 'hub-failover-command-accepted-v1';
  readonly commandId: string;
  readonly publicationId: string;
  readonly primaryPeerId: string;
  readonly backupPeerId: string;
  readonly previousRouteEpoch: number;
  readonly routeEpoch: number;
  readonly expiresAtMs: number;
}

export interface PeerOverlayParentPathPort {
  readonly peerId: string;
  connectControl(): Promise<void>;
  setBulkEnabled(enabled: boolean): Promise<void>;
  close(): void;
}

export interface PeerOverlayParentPathFactory {
  create(peerId: string): PeerOverlayParentPathPort;
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/;
const MAX_DEDUPLICATION_ENTRIES = 4_096;

/** Applies Hub-fenced failover while keeping the backup control-only. */
export class PeerOverlayParentFailover {
  private primary: PeerOverlayParentPathPort | null = null;
  private backup: PeerOverlayParentPathPort | null = null;
  private routeEpoch = 0;
  private readonly commands = new Set<string>();
  private readonly frames = new Set<string>();

  constructor(
    private readonly factory: PeerOverlayParentPathFactory,
    private readonly clock: () => number = () => Date.now(),
    private readonly maximumSwitchMs = 3_000,
  ) {
    if (maximumSwitchMs < 1 || maximumSwitchMs > 30_000) throw new Error('peer_failover_budget_invalid');
  }

  async initialize(primaryPeerId: string, backupPeerId: string | null, routeEpoch: number): Promise<void> {
    if (!ID_RE.test(primaryPeerId) || (backupPeerId !== null && !ID_RE.test(backupPeerId))
        || primaryPeerId === backupPeerId || !Number.isSafeInteger(routeEpoch) || routeEpoch < 1) {
      throw new Error('peer_failover_initial_state_invalid');
    }
    const primary = this.factory.create(primaryPeerId);
    await primary.connectControl();
    await primary.setBulkEnabled(true);
    const backup = backupPeerId === null ? null : this.factory.create(backupPeerId);
    if (backup) {
      await backup.connectControl();
      await backup.setBulkEnabled(false);
    }
    this.primary = primary;
    this.backup = backup;
    this.routeEpoch = routeEpoch;
  }

  async apply(command: AcceptedPeerFailoverCommand): Promise<void> {
    this.validateCommand(command);
    if (!this.primary || !this.backup || this.primary.peerId !== command.primaryPeerId
        || this.backup.peerId !== command.backupPeerId) throw new Error('peer_failover_path_mismatch');
    const startedAt = this.clock();
    await this.backup.connectControl();
    await this.primary.setBulkEnabled(false);
    try {
      await this.backup.setBulkEnabled(true);
      if (this.clock() - startedAt > this.maximumSwitchMs) throw new Error('peer_failover_deadline_exceeded');
    } catch (error) {
      await this.primary.setBulkEnabled(true);
      throw error;
    }
    this.primary.close();
    this.primary = this.backup;
    this.backup = null;
    this.routeEpoch = command.routeEpoch;
    this.commands.add(command.commandId);
  }

  acceptFrame(messageId: string): boolean {
    if (!ID_RE.test(messageId)) throw new Error('peer_failover_frame_id_invalid');
    if (this.frames.has(messageId)) return false;
    if (this.frames.size >= MAX_DEDUPLICATION_ENTRIES) throw new Error('peer_failover_deduplication_budget_exceeded');
    this.frames.add(messageId);
    return true;
  }

  snapshot(): Readonly<Record<string, unknown>> {
    return Object.freeze({
      primaryPeerId: this.primary?.peerId ?? null,
      backupPeerId: this.backup?.peerId ?? null,
      routeEpoch: this.routeEpoch,
      permanentDoubleTrafficAllowed: false,
    });
  }

  private validateCommand(command: AcceptedPeerFailoverCommand): void {
    if (command.validation !== 'hub-failover-command-accepted-v1'
        || ![command.commandId, command.publicationId, command.primaryPeerId, command.backupPeerId]
          .every(value => ID_RE.test(value))
        || command.primaryPeerId === command.backupPeerId
        || command.previousRouteEpoch !== this.routeEpoch
        || command.routeEpoch !== this.routeEpoch + 1
        || command.expiresAtMs <= this.clock()
        || this.commands.has(command.commandId)) throw new Error('peer_failover_command_invalid');
  }
}
