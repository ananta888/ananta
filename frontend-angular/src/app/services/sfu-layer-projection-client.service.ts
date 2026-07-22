import { Injectable, OnDestroy, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { SfuBroadcastReceiverLayerControllerService } from './sfu-broadcast-receiver-layer-controller.service';
import {
  SfuLayerProjectionApiService,
  type SfuLayerProjectionApiResult,
} from './sfu-layer-projection-api.service';
import {
  SfuLayerProjectionValidator,
  type SfuProjectionValidationBinding,
  type ValidatedLayerProjection,
} from './sfu-layer-projection.validator';
import type { SfuSubscriptionLayerPort } from './sfu-room-session.ports';

export interface SfuRoomProjectionClientBinding {
  readonly tenantRef: string;
  readonly roomRef: string;
  readonly membershipEpoch: number;
  readonly admissionEpoch?: number;
}

export interface SfuPublisherProjectionClientBinding extends SfuRoomProjectionClientBinding {
  readonly publicationRef: string;
  readonly routeEpoch: number;
  readonly topologyEpoch: number;
  readonly keyEpoch: number;
  readonly sessionProjectionVersion: number;
}

export interface SfuReceiverProjectionClientBinding extends SfuRoomProjectionClientBinding {
  readonly receiverRef: string;
  readonly subscriptionRef: string;
  readonly publicationRef: string;
  readonly routeEpoch: number;
  readonly topologyEpoch: number;
  readonly keyEpoch: number;
  readonly sessionProjectionVersion: number;
}

interface PollState {
  released: boolean;
  cursor: number;
  etag?: string;
  timer: ReturnType<typeof setTimeout> | null;
  failures: number;
  validatorBinding: SfuProjectionValidationBinding;
}

/** Fetches projections and supplies only validated bindings to browser controllers. */
@Injectable({ providedIn: 'root' })
export class SfuLayerProjectionClientService implements OnDestroy {
  private readonly api = inject(SfuLayerProjectionApiService);
  private readonly validator = inject(SfuLayerProjectionValidator);
  private readonly receivers = inject(SfuBroadcastReceiverLayerControllerService);
  private readonly releases = new Set<() => void>();

  async loadRoom(binding: SfuRoomProjectionClientBinding): Promise<ValidatedLayerProjection | null> {
    return this.loadOnce({
      kind: 'room', tenantRef: binding.tenantRef, roomRef: binding.roomRef,
      subjectRef: binding.roomRef, membershipEpoch: binding.membershipEpoch,
      admissionEpoch: binding.admissionEpoch,
    });
  }

  async loadPublisher(binding: SfuPublisherProjectionClientBinding): Promise<ValidatedLayerProjection | null> {
    return this.loadOnce({
      kind: 'publisher', tenantRef: binding.tenantRef, roomRef: binding.roomRef,
      subjectRef: binding.publicationRef, membershipEpoch: binding.membershipEpoch,
      admissionEpoch: binding.admissionEpoch,
      routeEpoch: binding.routeEpoch, topologyEpoch: binding.topologyEpoch,
      keyEpoch: binding.keyEpoch, sessionProjectionVersion: binding.sessionProjectionVersion,
    });
  }

  async bindReceiver(
    binding: SfuReceiverProjectionClientBinding,
    port: SfuSubscriptionLayerPort,
  ): Promise<() => void> {
    const validatorBinding: SfuProjectionValidationBinding = {
      kind: 'receiver', tenantRef: binding.tenantRef, roomRef: binding.roomRef,
      subjectRef: binding.subscriptionRef, membershipEpoch: binding.membershipEpoch,
      admissionEpoch: binding.admissionEpoch,
      routeEpoch: binding.routeEpoch, topologyEpoch: binding.topologyEpoch,
      keyEpoch: binding.keyEpoch, sessionProjectionVersion: binding.sessionProjectionVersion,
    };
    const state: PollState = { released: false, cursor: 0, timer: null, failures: 0, validatorBinding };
    const release = () => {
      if (state.released) return;
      state.released = true;
      if (state.timer !== null) globalThis.clearTimeout(state.timer);
      this.receivers.stop(binding.subscriptionRef);
      this.validator.reset(validatorBinding);
      this.releases.delete(release);
    };
    this.releases.add(release);
    void this.pollReceiver(state, binding, port);
    return release;
  }

  ngOnDestroy(): void {
    for (const release of [...this.releases]) release();
  }

  private async loadOnce(binding: SfuProjectionValidationBinding): Promise<ValidatedLayerProjection | null> {
    try {
      const response = await firstValueFrom(this.api.read({
        kind: binding.kind, roomRef: binding.roomRef, subjectRef: binding.subjectRef,
        cursor: 0, waitMs: 0,
      }));
      if (response.status !== 'projection') return null;
      const validated = await this.validator.validateAsync(response, binding);
      return validated.valid ? validated.projection : null;
    } catch {
      return null;
    }
  }

  private async pollReceiver(
    state: PollState,
    binding: SfuReceiverProjectionClientBinding,
    port: SfuSubscriptionLayerPort,
  ): Promise<void> {
    if (state.released) return;
    let nextDelay = 1000;
    try {
      const response: SfuLayerProjectionApiResult = await firstValueFrom(this.api.read({
        kind: 'receiver', roomRef: binding.roomRef, subjectRef: binding.subscriptionRef,
        cursor: state.cursor, etag: state.etag, waitMs: 1500,
      }));
      if (state.released) return;
      if (response.status === 'projection') {
        const validated = await this.validator.validateAsync(
          response, state.validatorBinding, Date.now(), false,
        );
        if (state.released) return;
        if (!validated.valid) {
          this.receivers.fallback(binding.subscriptionRef);
          nextDelay = 2000;
        } else {
          const applied = await this.receivers.bindAndApply({
            subscriptionRef: binding.subscriptionRef, publicationRef: binding.publicationRef,
            port, projection: validated.projection,
          });
          if (state.released) {
            this.receivers.stop(binding.subscriptionRef);
            return;
          }
          if (!applied || !this.validator.commit(validated.projection)) {
            this.receivers.fallback(binding.subscriptionRef);
            nextDelay = 2000;
          } else {
            state.cursor = response.cursor;
            state.etag = response.etag ?? state.etag;
            state.failures = 0;
          }
        }
      }
    } catch {
      state.failures = Math.min(5, state.failures + 1);
      nextDelay = Math.min(5000, 250 * (2 ** state.failures));
      this.receivers.fallback(binding.subscriptionRef);
    }
    if (!state.released) {
      state.timer = globalThis.setTimeout(() => {
        state.timer = null;
        void this.pollReceiver(state, binding, port);
      }, nextDelay);
    }
  }
}
