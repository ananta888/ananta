/**
 * Share-session read models (session, participants, chat, active state),
 * chat wire/plaintext shapes and membership mutation fences.
 */
import type { PairControlPlaneKind } from './pair-session-binding.store';

export interface ShareSession {
  id: string;
  /** Canonical, server-issued peer identity for this exact control plane. */
  local_peer_id?: string;
  title: string;
  /** Present only when the current membership is allowed to invite another peer. */
  invite_code?: string;
  mode: string;
  transport: string;
  permissions: Record<string, boolean>;
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
  /** Legacy/Hub identity field; compact v2 catalog rows deliberately omit it. */
  owner_user_id?: string;
  tenant_id?: string;
  permissions_version?: number;
  security_epoch?: number | null;
  security_contract_version?: number;
  security_mode?: string;
  identity_binding_version?: number;
  /** Server-validated role for an exact device-selected list item. */
  local_role?: 'owner' | 'participant';
  /** Server-authoritative runtime state for this exact v2 device membership. */
  local_runtime_state?: 'active' | 'parked';
  /** Canonical owner device peer id for v2 role verification. */
  owner_peer_id?: string;
  participant_count?: number;
  /** Server-issued, session-scoped label that does not expose a peer/device id. */
  peer_label?: string;
  participants?: readonly ShareParticipant[];
}

export interface ShareSessionCatalogEntry {
  readonly session: ShareSession;
  readonly role: 'owner' | 'participant';
}

export interface ShareParticipant {
  id: string;
  user_id: string;
  account_id?: string;
  peer_id?: string;
  device_id: string;
  joined_at: number;
  last_seen_at: number | null;
  revoked_at: number | null;
  permissions: Record<string, boolean>;
}

export interface ShareChatMessage {
  id: string;
  session_id: string;
  sender_id: string;
  text: string;
  created_at: number;
  visibility: string;
}

export interface StrictShareChatWireMessage {
  id: string;
  encrypted_payload: string;
}

export interface LegacyShareChatWireMessage {
  id?: unknown;
  session_id?: unknown;
  share_session_id?: unknown;
  sender_id?: unknown;
  from_id?: unknown;
  text?: unknown;
  created_at?: unknown;
  visibility?: unknown;
}

export interface StrictChatPlaintext {
  version: 1;
  id: string;
  sessionId: string;
  senderUserId: string;
  text: string;
  createdAt: number;
  visibility: 'room';
}

export interface ActiveShareState {
  session: ShareSession | null;
  participants: ShareParticipant[];
  messages: ShareChatMessage[];
  cursor: string;
  role: 'owner' | 'participant' | null;
}

export type PendingMembershipAuthority = PairControlPlaneKind | 'unknown';

export interface MembershipMutationFence {
  readonly serial: number;
  readonly kind: 'create' | 'join';
  readonly authority: PendingMembershipAuthority;
  readonly sourceSessionId: string;
  readonly sourceGeneration: number;
}

export type PublicPairRuntimeState =
  | 'idle'
  | 'public_pending'
  | 'hub_pending'
  | 'unknown_pending'
  | 'public'
  | 'hub'
  | 'unknown';
