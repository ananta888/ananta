package com.ananta.mobile.voice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class VoiceCaptureDurationContractTest {
    private static final int DEFAULT_SECONDS = 120;
    private static final int EIGHT_HOURS_SECONDS = 28_800;
    private static final long EIGHT_HOURS_PCM_BYTES = 921_600_000L;

    @Test
    public void microphoneDurationDefaultsAndClampsWithoutIntegerOverflow() {
        assertEquals(DEFAULT_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(null));
        assertEquals(DEFAULT_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(0));
        assertEquals(DEFAULT_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(-1));
        assertEquals(DEFAULT_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(1.5d));
        assertEquals(DEFAULT_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(Double.NaN));
        assertEquals(1, VoiceCapturePlugin.boundedMaxSeconds(1));
        assertEquals(EIGHT_HOURS_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(28_800));
        assertEquals(EIGHT_HOURS_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(28_801));
        assertEquals(EIGHT_HOURS_SECONDS, VoiceCapturePlugin.boundedMaxSeconds(Long.MAX_VALUE));
        assertEquals(
                EIGHT_HOURS_PCM_BYTES,
                VoiceCapturePlugin.maximumCaptureBytes(EIGHT_HOURS_SECONDS)
        );
    }

    @Test
    public void playbackPluginUsesTheSameOptionalDurationContract() {
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(null));
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(0));
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(-1));
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(1.5d));
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(Double.POSITIVE_INFINITY));
        assertEquals(1, PlaybackAudioCapturePlugin.boundedMaxSeconds(1));
        assertEquals(EIGHT_HOURS_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(28_800));
        assertEquals(EIGHT_HOURS_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(Integer.MAX_VALUE));
        assertEquals(EIGHT_HOURS_SECONDS, PlaybackAudioCapturePlugin.boundedMaxSeconds(Long.MAX_VALUE));
    }

    @Test
    public void playbackServiceDefendsTheDurationAndByteBudgetBoundary() {
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCaptureService.boundedMaxSeconds(0));
        assertEquals(DEFAULT_SECONDS, PlaybackAudioCaptureService.boundedMaxSeconds(-1));
        assertEquals(1, PlaybackAudioCaptureService.boundedMaxSeconds(1));
        assertEquals(EIGHT_HOURS_SECONDS, PlaybackAudioCaptureService.boundedMaxSeconds(28_800));
        assertEquals(EIGHT_HOURS_SECONDS, PlaybackAudioCaptureService.boundedMaxSeconds(28_801));
        assertEquals(EIGHT_HOURS_SECONDS, PlaybackAudioCaptureService.boundedMaxSeconds(Integer.MAX_VALUE));
        assertEquals(
                EIGHT_HOURS_PCM_BYTES,
                PlaybackAudioCaptureService.maximumCaptureBytes(EIGHT_HOURS_SECONDS)
        );
    }
}
