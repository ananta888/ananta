import { VoiceLongRunLivePreviewPort } from './voice-long-run-live-preview';
import { VoiceLongRunObserver } from './voice-long-run.policy';
import { VoiceLongRunCreateRequest } from './voice.models';

/**
 * Starts the non-authoritative live preview for a run and forwards its events
 * to the current observer. A preview failure is reported and swallowed: the
 * encrypted segment spool and Hub-owned ASR/correction flow keep running.
 */
export async function startVoiceLongRunLivePreview(
  livePreview: VoiceLongRunLivePreviewPort,
  context: Readonly<{
    hubUrl: string;
    runId: string;
    request: VoiceLongRunCreateRequest;
    initialSegmentSequence: number;
  }>,
  observer: () => VoiceLongRunObserver,
): Promise<void> {
  try {
    await livePreview.start({
      hubUrl: context.hubUrl,
      liveRunId: context.runId,
      profileId: context.request.profile_id,
      configurationSessionId: context.request.configuration_session_id,
      language: context.request.language,
      segmentDurationSeconds: context.request.segment_duration_seconds,
      initialSegmentSequence: context.initialSegmentSequence,
    }, {
      segmentStarted: (segmentSequence) => (
        observer().livePreviewStarted?.(segmentSequence)
      ),
      preview: (update) => observer().livePreview?.(update),
      error: (error) => observer().livePreviewUnavailable?.(error),
    });
  } catch (error) {
    observer().livePreviewUnavailable?.(error);
  }
}
