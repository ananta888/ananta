import type { MembershipMutationFence, PendingMembershipAuthority } from './share-session.types';

/** Exact local session context a fenced operation was started from. */
export interface ShareSessionFenceContext {
  readonly sessionId: string;
  readonly generation: number;
}

export interface SessionSwitchFence {
  readonly serial: number;
  readonly sourceSessionId: string;
  readonly targetSessionId: string;
  readonly sourceGeneration: number;
}

/**
 * Single-flight fences for membership mutations (create/join) and session
 * switches. An operation stays current only while it is the latest of its
 * kind and the active session id and generation are unchanged; otherwise
 * the next assertion throws a stable reason code.
 */
export class ShareSessionMutationFences {
  private membershipSerial = 0;
  private membership: Readonly<MembershipMutationFence> | null = null;
  private switchSerial = 0;
  private sessionSwitch: Readonly<SessionSwitchFence> | null = null;

  constructor(
    private readonly currentContext: () => ShareSessionFenceContext,
    private readonly onMembershipMutationChanged: () => void,
  ) {}

  get activeMembershipMutation(): Readonly<MembershipMutationFence> | null { return this.membership; }

  get pending(): boolean {
    return this.membership !== null || this.sessionSwitch !== null;
  }

  async runMembershipMutation<T>(
    kind: 'create' | 'join',
    authority: PendingMembershipAuthority,
    operation: (mutation: Readonly<MembershipMutationFence>) => Promise<T>,
  ): Promise<T> {
    if (this.pending) {
      throw new Error('pair_session_mutation_in_progress');
    }
    const context = this.currentContext();
    const mutation = Object.freeze({
      serial: ++this.membershipSerial,
      kind,
      authority,
      sourceSessionId: context.sessionId,
      sourceGeneration: context.generation,
    });
    this.membership = mutation;
    this.onMembershipMutationChanged();
    try {
      return await operation(mutation);
    } finally {
      if (this.membership?.serial === mutation.serial) {
        this.membership = null;
        this.onMembershipMutationChanged();
      }
    }
  }

  assertMembershipMutationCurrent(mutation: Readonly<MembershipMutationFence>): void {
    if (this.membership?.serial !== mutation.serial) {
      throw new Error('pair_session_mutation_overtaken');
    }
    const context = this.currentContext();
    if (
      context.generation !== mutation.sourceGeneration
      || context.sessionId !== mutation.sourceSessionId
    ) throw new Error('pair_session_mutation_context_changed');
  }

  beginSessionSwitch(targetSessionId: string): Readonly<SessionSwitchFence> {
    const context = this.currentContext();
    const operation = Object.freeze({
      serial: ++this.switchSerial,
      sourceSessionId: context.sessionId,
      targetSessionId,
      sourceGeneration: context.generation,
    });
    this.sessionSwitch = operation;
    return operation;
  }

  assertSessionSwitchCurrent(operation: Readonly<SessionSwitchFence>): void {
    if (this.sessionSwitch?.serial !== operation.serial) {
      throw new Error('pair_session_switch_overtaken');
    }
    const context = this.currentContext();
    if (
      context.generation !== operation.sourceGeneration
      || context.sessionId !== operation.sourceSessionId
    ) throw new Error('pair_session_switch_context_changed');
  }

  endSessionSwitch(operation: Readonly<SessionSwitchFence>): void {
    if (this.sessionSwitch?.serial === operation.serial) this.sessionSwitch = null;
  }
}
