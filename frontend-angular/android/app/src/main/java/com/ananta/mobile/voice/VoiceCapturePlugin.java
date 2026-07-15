package com.ananta.mobile.voice;

import android.Manifest;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;

import java.util.Locale;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * Platform capture adapter for Hub-owned Voice streaming.
 *
 * <p>This plugin deliberately performs no recognition and owns no model choice.
 * It emits bounded PCM16/16 kHz/mono chunks so the authenticated Angular client
 * can send them to the Hub, which remains the policy and orchestration owner.</p>
 */
@CapacitorPlugin(
        name = "VoiceCapture",
        permissions = {
                @Permission(strings = {Manifest.permission.RECORD_AUDIO}, alias = "microphone")
        }
)
public final class VoiceCapturePlugin extends Plugin {
    private static final int SAMPLE_RATE = 16_000;
    private static final int BYTES_PER_SAMPLE = 2;
    private static final int MIN_CHUNK_MILLISECONDS = 100;
    private static final int MAX_CHUNK_MILLISECONDS = 1_000;
    private static final int DEFAULT_CHUNK_MILLISECONDS = 500;
    private static final int DEFAULT_MAX_SECONDS = 120;
    private static final int MAX_SESSION_SECONDS = 28_800;
    private static final long BYTES_PER_SECOND = (long) SAMPLE_RATE * BYTES_PER_SAMPLE;

    private final Object captureLock = new Object();
    private volatile boolean capturing;
    private AudioRecord recorder;
    private Thread captureThread;
    private int nextSequence;
    private long capturedBytes;

    @PluginMethod
    public void getStatus(PluginCall call) {
        JSObject result = new JSObject();
        synchronized (captureLock) {
            result.put("capturing", capturing);
            result.put("nextSequence", nextSequence);
            result.put("capturedBytes", capturedBytes);
        }
        result.put("sampleRate", SAMPLE_RATE);
        result.put("channels", 1);
        result.put("encoding", "pcm_s16le");
        result.put("microphonePermission", permissionValue());
        call.resolve(result);
    }

    @PluginMethod
    public void requestMicrophonePermission(PluginCall call) {
        if (getPermissionState("microphone") == PermissionState.GRANTED) {
            JSObject result = new JSObject();
            result.put("state", "granted");
            call.resolve(result);
            return;
        }
        requestPermissionForAlias("microphone", call, "microphonePermissionResult");
    }

    @SuppressWarnings("unused")
    @PermissionCallback
    public void microphonePermissionResult(PluginCall call) {
        JSObject result = new JSObject();
        result.put("state", permissionValue());
        call.resolve(result);
    }

    @PluginMethod
    public void start(PluginCall call) {
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            call.reject("Microphone permission is required.");
            return;
        }

        Integer requestedSampleRate = call.getInt("sampleRate");
        if (requestedSampleRate != null && requestedSampleRate != SAMPLE_RATE) {
            call.reject("Hub Voice capture requires exactly 16000 Hz PCM.");
            return;
        }
        int chunkMilliseconds = bounded(
                call.getInt("chunkMilliseconds"),
                DEFAULT_CHUNK_MILLISECONDS,
                MIN_CHUNK_MILLISECONDS,
                MAX_CHUNK_MILLISECONDS
        );
        int maxSeconds = boundedMaxSeconds(call.getData().opt("maxSeconds"));

        final int channelConfig = AudioFormat.CHANNEL_IN_MONO;
        final int encoding = AudioFormat.ENCODING_PCM_16BIT;
        int minimumBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, channelConfig, encoding);
        if (minimumBuffer <= 0) {
            call.reject("AudioRecord buffer initialization failed.");
            return;
        }
        int chunkBytes = evenBytes(SAMPLE_RATE * BYTES_PER_SAMPLE * chunkMilliseconds / 1_000);
        int recorderBuffer = Math.max(minimumBuffer * 2, chunkBytes * 2);
        AudioRecord nextRecorder;
        try {
            nextRecorder = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE,
                    channelConfig,
                    encoding,
                    recorderBuffer
            );
        } catch (RuntimeException error) {
            call.reject("AudioRecord could not be created.", error);
            return;
        }
        if (nextRecorder.getState() != AudioRecord.STATE_INITIALIZED) {
            nextRecorder.release();
            call.reject("AudioRecord could not be initialized.");
            return;
        }

        synchronized (captureLock) {
            if (capturing || (captureThread != null && captureThread.isAlive())) {
                nextRecorder.release();
                call.reject("Voice capture is already running or still stopping.");
                return;
            }
            recorder = nextRecorder;
            capturing = true;
            nextSequence = 0;
            capturedBytes = 0;
            captureThread = new Thread(
                    () -> captureLoop(nextRecorder, chunkBytes, maxSeconds),
                    "ananta-hub-voice-capture"
            );
            captureThread.start();
        }

        JSObject result = new JSObject();
        result.put("started", true);
        result.put("sampleRate", SAMPLE_RATE);
        result.put("channels", 1);
        result.put("encoding", "pcm_s16le");
        result.put("chunkMilliseconds", chunkMilliseconds);
        result.put("maxSeconds", maxSeconds);
        call.resolve(result);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        Thread thread;
        synchronized (captureLock) {
            capturing = false;
            thread = captureThread;
            stopRecorderQuietly(recorder);
        }
        if (thread != null && thread != Thread.currentThread()) {
            try {
                thread.join(2_500L);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
        }
        JSObject result = captureSummary();
        result.put("stopped", thread == null || !thread.isAlive());
        call.resolve(result);
    }

    @Override
    protected void handleOnDestroy() {
        synchronized (captureLock) {
            capturing = false;
            stopRecorderQuietly(recorder);
        }
        super.handleOnDestroy();
    }

    private void captureLoop(AudioRecord activeRecorder, int chunkBytes, int maxSeconds) {
        String stopReason = "stopped";
        long maxBytes = maximumCaptureBytes(maxSeconds);
        try {
            activeRecorder.startRecording();
            while (capturing && capturedBytes < maxBytes) {
                int remainingBudget = (int) Math.min(chunkBytes, maxBytes - capturedBytes);
                byte[] chunk = new byte[evenBytes(remainingBudget)];
                int offset = 0;
                while (capturing && offset < chunk.length) {
                    int read = activeRecorder.read(chunk, offset, chunk.length - offset);
                    if (read == AudioRecord.ERROR_INVALID_OPERATION || read == AudioRecord.ERROR_BAD_VALUE) {
                        throw new IllegalStateException("AudioRecord read failed: " + read);
                    }
                    if (read <= 0) {
                        continue;
                    }
                    offset += read;
                }
                int accepted = evenBytes(offset);
                if (accepted > 0) {
                    emitChunk(chunk, accepted);
                }
            }
            if (capturedBytes >= maxBytes) {
                stopReason = "safety_limit";
            }
        } catch (RuntimeException error) {
            if (capturing) {
                stopReason = "capture_error";
                JSObject event = new JSObject();
                event.put("message", String.valueOf(error.getMessage()));
                notifyListeners("voiceCaptureError", event);
            }
        } finally {
            stopRecorderQuietly(activeRecorder);
            activeRecorder.release();
            synchronized (captureLock) {
                if (recorder == activeRecorder) {
                    recorder = null;
                }
                if (captureThread == Thread.currentThread()) {
                    capturing = false;
                    captureThread = null;
                }
            }
            JSObject stopped = captureSummary();
            stopped.put("reason", stopReason);
            notifyListeners("voiceCaptureStopped", stopped);
        }
    }

    private void emitChunk(byte[] chunk, int length) {
        byte[] payload;
        if (length == chunk.length) {
            payload = chunk;
        } else {
            payload = new byte[length];
            System.arraycopy(chunk, 0, payload, 0, length);
        }
        int sequence;
        long total;
        synchronized (captureLock) {
            sequence = nextSequence++;
            capturedBytes += payload.length;
            total = capturedBytes;
        }
        JSObject event = new JSObject();
        event.put("sequence", sequence);
        event.put("dataBase64", Base64.encodeToString(payload, Base64.NO_WRAP));
        event.put("byteLength", payload.length);
        event.put("capturedBytes", total);
        event.put("capturedMilliseconds", total * 1_000L / BYTES_PER_SECOND);
        event.put("sampleRate", SAMPLE_RATE);
        event.put("channels", 1);
        event.put("encoding", "pcm_s16le");
        notifyListeners("voicePcmChunk", event);
    }

    private JSObject captureSummary() {
        JSObject result = new JSObject();
        synchronized (captureLock) {
            result.put("capturing", capturing);
            result.put("nextSequence", nextSequence);
            result.put("capturedBytes", capturedBytes);
            result.put(
                    "capturedMilliseconds",
                    capturedBytes * 1_000L / BYTES_PER_SECOND
            );
        }
        return result;
    }

    private String permissionValue() {
        return getPermissionState("microphone").toString().toLowerCase(Locale.US);
    }

    private static int bounded(Integer requested, int defaultValue, int minimum, int maximum) {
        int value = requested == null ? defaultValue : requested;
        return Math.max(minimum, Math.min(maximum, value));
    }

    static int boundedMaxSeconds(Object requested) {
        if (!(requested instanceof Number)) return DEFAULT_MAX_SECONDS;
        double value = ((Number) requested).doubleValue();
        if (
                Double.isNaN(value)
                        || Double.isInfinite(value)
                        || value <= 0
                        || value != Math.rint(value)
        ) {
            return DEFAULT_MAX_SECONDS;
        }
        if (value >= MAX_SESSION_SECONDS) return MAX_SESSION_SECONDS;
        return (int) value;
    }

    static long maximumCaptureBytes(int maxSeconds) {
        return BYTES_PER_SECOND * (long) maxSeconds;
    }

    private static int evenBytes(int value) {
        return Math.max(0, value - (value % BYTES_PER_SAMPLE));
    }

    private static void stopRecorderQuietly(AudioRecord value) {
        if (value == null) return;
        try {
            if (value.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                value.stop();
            }
        } catch (RuntimeException ignored) {
            // The capture loop owns final release; stop only unblocks a pending read.
        }
    }
}
