import { Injectable } from '@angular/core';

import type {
  PublisherLayerProjectionContract,
  SfuBroadcastJsonObject,
  ValidatedSfuBroadcastContract,
} from './sfu-broadcast-contracts';
import type {
  SfuPublicationPort,
  SfuPublishedTrack,
  SfuPublisherEncodingProjection,
  SfuValidatedPublisherProjection,
} from './sfu-room-session.ports';

type ValidatedPublisherContract = Extract<ValidatedSfuBroadcastContract, {
  contractId: 'ananta.sfu-publisher-layer-projection.v1';
}>;

@Injectable({ providedIn: 'root' })
export class SfuBroadcastPublicationControllerService {
  private readonly byPublication = new Map<string, SfuPublishedTrack>();
  private readonly publicationBySource = new Map<string, string>();

  async apply(
    port: SfuPublicationPort,
    contract: ValidatedPublisherContract,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    if (!port.publishProjected) throw new Error('sfu_projected_publication_unsupported');
    const projection = normalizeProjection(contract.document);
    if (track.kind !== projection.mediaKind) throw new Error('sfu_publication_kind_mismatch');
    const currentId = this.publicationBySource.get(projection.source);
    if (currentId) {
      const current = this.byPublication.get(currentId);
      if (current && current.projectionVersion !== undefined
          && projection.projectionVersion <= current.projectionVersion) {
        throw new Error('sfu_publisher_projection_replay');
      }
      if (current) await this.stop(port, current.publicationId);
    }
    const published = await port.publishProjected(projection, track);
    this.byPublication.set(published.publicationId, published);
    this.publicationBySource.set(projection.source, published.publicationId);
    track.onended = () => { void this.stop(port, published.publicationId); };
    return published;
  }

  async replace(
    port: SfuPublicationPort,
    publicationId: string,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    const current = this.byPublication.get(publicationId);
    if (!current) throw new Error('sfu_publication_missing');
    if (!port.replaceProjectedTrack) throw new Error('sfu_publication_replace_unsupported');
    if (track.kind !== current.track.kind) throw new Error('sfu_publication_kind_mismatch');
    const replaced = await port.replaceProjectedTrack(current, track);
    this.byPublication.set(publicationId, replaced);
    track.onended = () => { void this.stop(port, publicationId); };
    return replaced;
  }

  async setPaused(port: SfuPublicationPort, publicationId: string, paused: boolean): Promise<void> {
    const current = this.byPublication.get(publicationId);
    if (!current) throw new Error('sfu_publication_missing');
    if (!port.setProjectedTrackPaused) throw new Error('sfu_publication_pause_unsupported');
    await port.setProjectedTrackPaused(current, paused);
  }

  async stop(port: SfuPublicationPort, publicationId: string): Promise<boolean> {
    const current = this.byPublication.get(publicationId);
    if (!current) return false;
    this.byPublication.delete(publicationId);
    for (const [source, id] of this.publicationBySource) {
      if (id === publicationId) this.publicationBySource.delete(source);
    }
    await port.unpublish(current);
    return true;
  }

  async stopAll(port: SfuPublicationPort): Promise<void> {
    const ids = [...this.byPublication.keys()].sort();
    let firstFailure: unknown = null;
    for (const id of ids) {
      try { await this.stop(port, id); } catch (error) { firstFailure ??= error; }
    }
    if (firstFailure) throw firstFailure;
  }

}

function normalizeProjection(document: PublisherLayerProjectionContract): SfuValidatedPublisherProjection {
  if (document.resolution !== 'planned' || document.safe_outcome !== 'apply_projection') {
    throw new Error(document.safe_outcome === 'deny'
      ? 'sfu_publisher_projection_denied'
      : 'sfu_publisher_projection_fallback_required');
  }
  const source = document.media_kind === 'audio'
    ? 'microphone' : document.media_kind === 'screenshare' ? 'screen' : 'camera';
  const encodings = document.encoding_plan.map(parseEncoding);
  if (encodings.length < 1 || encodings.length > 3) {
    throw new Error('sfu_publisher_projection_encoding_invalid');
  }
  const codec = encodings[0].codecClass;
  if (encodings.some(value => value.codecClass !== codec)) {
    throw new Error('sfu_publisher_projection_codec_mixed');
  }
  return Object.freeze({
    validation: 'hub-contract-accepted-v1',
    publicationId: document.publication_ref,
    source,
    mediaKind: document.media_kind === 'audio' ? 'audio' : 'video',
    projectionVersion: document.projection_version,
    routeEpoch: document.route_epoch,
    keyEpoch: document.key_epoch,
    encodings: Object.freeze(encodings),
  });
}

function parseEncoding(raw: unknown): SfuPublisherEncodingProjection {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('sfu_publisher_projection_encoding_invalid');
  }
  const value = raw as SfuBroadcastJsonObject;
  const audio = value['encoding_class'] === 'audio_primary';
  return Object.freeze({
    encodingClass: String(value['encoding_class']) as SfuPublisherEncodingProjection['encodingClass'],
    codecClass: String(value['codec_class']) as SfuPublisherEncodingProjection['codecClass'],
    ridClass: String(value['rid_class']) as SfuPublisherEncodingProjection['ridClass'],
    scalabilityClass: String(value['scalability_class']) as SfuPublisherEncodingProjection['scalabilityClass'],
    maxBitrateBps: positiveInteger(value['max_bitrate_bps']),
    maxWidth: audio ? null : positiveInteger(value['max_width']),
    maxHeight: audio ? null : positiveInteger(value['max_height']),
    maxFps: audio ? null : positiveInteger(value['max_fps']),
  });
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new Error('sfu_publisher_projection_encoding_invalid');
  }
  return value as number;
}
