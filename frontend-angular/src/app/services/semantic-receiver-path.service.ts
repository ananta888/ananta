import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type SemanticReceiverPathPreference = 'auto' | 'ordinary' | 'sfu';
export type SemanticReceiverEffectivePath = 'ordinary' | 'sfu';

export interface SemanticReceiverIdentity {
  readonly receiverId: string;
  readonly label: string;
}

export interface SemanticReceiverPathView extends SemanticReceiverIdentity {
  readonly preference: SemanticReceiverPathPreference;
  readonly effectivePath: SemanticReceiverEffectivePath;
  readonly pendingHubConfirmation: boolean;
  readonly reasonCode: string;
}

export interface SemanticReceiverHubPathState {
  readonly sfuConnected: boolean;
  readonly sfuAuthorizedReceiverIds: ReadonlySet<string>;
  readonly sfuFeatureEnabled: boolean;
}

/**
 * Receiver-scoped desired/effective projection.  User preference never
 * expands an SFU audience; only a Hub-authorized active admission changes the
 * effective path.
 */
@Injectable({ providedIn: 'root' })
export class SemanticReceiverPathService {
  readonly rows$ = new BehaviorSubject<readonly SemanticReceiverPathView[]>(Object.freeze([]));
  private identities: readonly SemanticReceiverIdentity[] = Object.freeze([]);
  private hub: SemanticReceiverHubPathState = Object.freeze({
    sfuConnected: false,
    sfuAuthorizedReceiverIds: new Set<string>(),
    sfuFeatureEnabled: false,
  });
  private readonly preferences = new Map<string, SemanticReceiverPathPreference>();
  private readonly operations = new Map<string, Readonly<{
    status: 'pending' | 'failed'; reasonCode: string;
  }>>();

  setReceivers(receivers: readonly SemanticReceiverIdentity[]): void {
    const seen = new Set<string>();
    this.identities = Object.freeze(receivers.map(receiver => {
      if (!validId(receiver.receiverId) || seen.has(receiver.receiverId)) throw new Error('semantic_receiver_invalid');
      seen.add(receiver.receiverId);
      return Object.freeze({ receiverId: receiver.receiverId, label: String(receiver.label || receiver.receiverId) });
    }));
    for (const receiverId of this.preferences.keys()) if (!seen.has(receiverId)) this.preferences.delete(receiverId);
    for (const receiverId of this.operations.keys()) if (!seen.has(receiverId)) this.operations.delete(receiverId);
    this.emit();
  }

  setHubState(state: SemanticReceiverHubPathState): void {
    this.hub = Object.freeze({
      sfuConnected: state.sfuConnected === true,
      sfuAuthorizedReceiverIds: new Set(state.sfuAuthorizedReceiverIds),
      sfuFeatureEnabled: state.sfuFeatureEnabled === true,
    });
    this.emit();
  }

  request(receiverId: string, preference: SemanticReceiverPathPreference): void {
    if (!this.identities.some(value => value.receiverId === receiverId)) throw new Error('semantic_receiver_unknown');
    if (!['auto', 'ordinary', 'sfu'].includes(preference)) throw new Error('semantic_receiver_preference_invalid');
    this.preferences.set(receiverId, preference);
    this.operations.delete(receiverId);
    this.emit();
  }

  setOperationState(receiverId: string, pending: boolean, reasonCode?: string): void {
    if (!this.identities.some(value => value.receiverId === receiverId)) throw new Error('semantic_receiver_unknown');
    if (pending) {
      this.operations.set(receiverId, Object.freeze({
        status: 'pending', reasonCode: validReason(reasonCode) ? reasonCode! : 'receiver_path_hub_confirmation_required',
      }));
    } else if (validReason(reasonCode)) {
      this.operations.set(receiverId, Object.freeze({ status: 'failed', reasonCode: reasonCode! }));
    } else {
      this.operations.delete(receiverId);
    }
    this.emit();
  }

  clear(): void {
    this.identities = Object.freeze([]);
    this.preferences.clear();
    this.operations.clear();
    this.emit();
  }

  private emit(): void {
    const rows = this.identities.map(identity => {
      const preference = this.preferences.get(identity.receiverId) ?? 'auto';
      const hubSfu = this.hub.sfuFeatureEnabled
        && this.hub.sfuConnected
        && this.hub.sfuAuthorizedReceiverIds.has(identity.receiverId);
      const effectivePath: SemanticReceiverEffectivePath = hubSfu ? 'sfu' : 'ordinary';
      const desired = preference === 'auto' ? effectivePath : preference;
      const operation = this.operations.get(identity.receiverId);
      const pendingHubConfirmation = operation?.status === 'pending'
        || (operation?.status !== 'failed' && desired !== effectivePath);
      const reasonCode = operation?.reasonCode ?? (pendingHubConfirmation
        ? 'receiver_path_hub_confirmation_required'
        : effectivePath === 'sfu' ? 'receiver_path_sfu_admitted' : 'receiver_path_ordinary_safe_default');
      return Object.freeze({ ...identity, preference, effectivePath, pendingHubConfirmation, reasonCode });
    });
    this.rows$.next(Object.freeze(rows));
  }
}

function validId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function validReason(value: string | undefined): boolean {
  return typeof value === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(value);
}
