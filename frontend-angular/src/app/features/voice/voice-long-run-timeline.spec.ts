import {
  VoiceLongRunTimeline,
  voiceLongRunCorrectionFailureLabel,
  voiceLongRunRevisionLabel,
} from './voice-long-run-timeline';
import { VoiceLongRunResponse, VoiceLongRunSegment } from './voice.models';

describe('VoiceLongRunTimeline', () => {
  it('publishes provisional ASR immediately and replaces only its sequence with a higher revision', () => {
    const timeline = new VoiceLongRunTimeline();
    const provisional = timeline.apply(response(segment({
      sequence: 3,
      revision: 1,
      timeline_revision: 7,
      text_state: 'provisional',
      correction_status: 'pending',
      text: 'das ist ein roher texd',
    })));

    expect(provisional.segments).toHaveLength(1);
    expect(provisional.segments[0]).toEqual(expect.objectContaining({
      sequence: 3,
      revision: 1,
      text: 'das ist ein roher texd',
    }));
    expect(provisional.hasPendingRevisions).toBe(true);
    expect(voiceLongRunRevisionLabel(provisional.segments[0])).toBe('vorläufig');

    const corrected = timeline.apply(response(segment({
      sequence: 3,
      revision: 2,
      timeline_revision: 8,
      text_state: 'final',
      correction_status: 'completed',
      text: 'Das ist ein korrigierter Text.',
    })));

    expect(corrected.segments).toHaveLength(1);
    expect(corrected.segments[0].text).toBe('Das ist ein korrigierter Text.');
    expect(corrected.composedTranscript).toBe('Das ist ein korrigierter Text.');
    expect(corrected.hasPendingRevisions).toBe(false);
    expect(voiceLongRunRevisionLabel(corrected.segments[0])).toBe('korrigiert');
  });

  it('ignores lower and conflicting equal revisions without rolling final state back', () => {
    const timeline = new VoiceLongRunTimeline();
    timeline.apply(response(segment({
      sequence: 0, revision: 2, timeline_revision: 12, text_state: 'final',
      correction_status: 'completed', text: 'Finaler Text',
    })));
    timeline.apply(response(segment({
      sequence: 0, revision: 1, timeline_revision: 10, text_state: 'provisional',
      correction_status: 'pending', text: 'Verspätetes Rohresultat',
    })));
    const snapshot = timeline.apply(response(segment({
      sequence: 0, revision: 2, timeline_revision: 11, text_state: 'provisional',
      correction_status: 'pending', text: 'Inkonsistente gleiche Revision',
    })));

    expect(snapshot.segments[0]).toEqual(expect.objectContaining({
      revision: 2,
      timeline_revision: 12,
      text_state: 'final',
      correction_status: 'completed',
      text: 'Finaler Text',
    }));
  });

  it('orders out-of-order deltas by sequence and deduplicates only real timeline overlap', () => {
    const timeline = new VoiceLongRunTimeline();
    timeline.apply(response(segment({
      sequence: 1, revision: 1, timeline_revision: 2, text_state: 'final',
      correction_status: 'skipped', started_at_ms: 9_000, ended_at_ms: 20_000,
      text: 'nächsten Release und prüfen danach.',
    })));
    const snapshot = timeline.apply(response(segment({
      sequence: 0, revision: 1, timeline_revision: 1, text_state: 'final',
      correction_status: 'skipped', started_at_ms: 0, ended_at_ms: 10_000,
      text: 'Wir planen den nächsten Release.',
    })));

    expect(snapshot.segments.map((item) => item.sequence)).toEqual([0, 1]);
    expect(snapshot.segments[1].display_text).toBe('und prüfen danach.');
    expect(snapshot.composedTranscript).toBe('Wir planen den nächsten Release. und prüfen danach.');

    const withoutOverlap = new VoiceLongRunTimeline();
    withoutOverlap.apply(response(segment({
      sequence: 0, revision: 1, timeline_revision: 1, text_state: 'final',
      correction_status: 'skipped', started_at_ms: 0, ended_at_ms: 10_000,
      text: 'Ja ja',
    })));
    const repeated = withoutOverlap.apply(response(segment({
      sequence: 1, revision: 1, timeline_revision: 2, text_state: 'final',
      correction_status: 'skipped', started_at_ms: 10_000, ended_at_ms: 20_000,
      text: 'ja, das stimmt.',
    })));
    expect(repeated.composedTranscript).toBe('Ja ja ja, das stimmt.');
  });

  it('keeps raw text visible when optional correction fails or is skipped', () => {
    const timeline = new VoiceLongRunTimeline();
    const failed = timeline.apply(response(segment({
      sequence: 0, revision: 2, timeline_revision: 3, text_state: 'final',
      correction_status: 'failed', text: 'Verständlicher Rohtext',
    })));

    expect(failed.composedTranscript).toBe('Verständlicher Rohtext');
    expect(failed.hasPendingRevisions).toBe(false);
    expect(voiceLongRunRevisionLabel(failed.segments[0])).toBe('final · Korrektur fehlgeschlagen');
  });

  it('renders a human-readable correction failure reason from the Hub code', () => {
    const timeline = new VoiceLongRunTimeline();
    const failed = timeline.apply(response(segment({
      sequence: 0,
      revision: 2,
      timeline_revision: 3,
      text_state: 'final',
      correction_status: 'failed',
      correction_failure_code: 'corrector_engine_failed',
      text: 'Verständlicher Rohtext',
    })));

    expect(voiceLongRunRevisionLabel(failed.segments[0]))
      .toBe('final · Korrektur fehlgeschlagen: Korrekturmodell fehlgeschlagen');
  });

  it.each([
    ['corrector_provider_output_invalid', 'Korrekturmodell lieferte eine ungültige Ausgabe'],
    ['corrector_provider_response_invalid', 'Korrektur-Provider lieferte eine ungültige Antwort'],
    ['corrector_provider_http_error', 'Korrektur-Provider meldete einen HTTP-Fehler'],
    ['corrector_provider_unavailable', 'Korrektur-Provider nicht verfügbar'],
    ['corrector_provider_timeout', 'Zeitlimit des Korrektur-Providers überschritten'],
    ['corrector_provider_request_failed', 'Anfrage an Korrektur-Provider fehlgeschlagen'],
    ['correction_execution_failed', 'Korrekturausführung fehlgeschlagen'],
    ['correction_execution_timeout', 'Zeitlimit der Korrekturausführung überschritten'],
  ])('describes stable correction failure code %s', (code, expected) => {
    expect(voiceLongRunCorrectionFailureLabel(code)).toBe(expected);
  });

  it('does not treat a textless heartbeat projection as a content revision', () => {
    const timeline = new VoiceLongRunTimeline();
    timeline.apply(response(segment({
      sequence: 0, revision: 1, timeline_revision: 4, text_state: 'provisional',
      correction_status: 'pending', text: 'Sichtbarer Rohtext',
    })));
    const heartbeat = timeline.apply(response(segment({
      sequence: 0, revision: 2, timeline_revision: 5, text_state: 'final',
      correction_status: 'completed', text: null,
    })));

    expect(heartbeat.composedTranscript).toBe('Sichtbarer Rohtext');
    expect(heartbeat.highestTimelineRevision).toBe(4);
    expect(heartbeat.hasPendingRevisions).toBe(true);
  });

  it('leaves synthetic gap rows to the dedicated gap projection', () => {
    const timeline = new VoiceLongRunTimeline();
    const snapshot = timeline.apply({
      run: { id: 'run-a', status: 'active', timeline_revision: 2 },
      segments: [{ sequence: 2, status: 'gap', text_state: 'none', revision: 0 }],
      gaps: [2],
    });

    expect(snapshot.segments).toEqual([]);
    expect(snapshot.hasPendingRevisions).toBe(false);
  });

  it('treats a persisted failed ASR segment as a terminal gap instead of pending text', () => {
    const timeline = new VoiceLongRunTimeline();
    const snapshot = timeline.apply(response(segment({
      sequence: 4,
      status: 'failed',
      failure_code: 'processing_lease_expired',
      revision: 0,
      timeline_revision: 9,
      text_state: 'none',
      correction_status: 'not_requested',
      text: null,
    })));

    expect(snapshot.hasPendingRevisions).toBe(false);
    expect(voiceLongRunRevisionLabel(snapshot.segments[0])).toBe('Lücke');
  });
});

function segment(overrides: Partial<VoiceLongRunSegment>): VoiceLongRunSegment {
  return {
    sequence: 0,
    status: 'completed',
    ...overrides,
  };
}

function response(item: VoiceLongRunSegment): VoiceLongRunResponse {
  return {
    run: { id: 'run-a', status: 'active', timeline_revision: item.timeline_revision },
    segments: [item],
    gaps: [],
  };
}
