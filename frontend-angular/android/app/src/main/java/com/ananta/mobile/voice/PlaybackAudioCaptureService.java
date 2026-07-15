package com.ananta.mobile.voice;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioPlaybackCaptureConfiguration;
import android.media.AudioRecord;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import androidx.annotation.Nullable;

import com.ananta.mobile.MainActivity;
import com.ananta.mobile.R;

/**
 * Foreground execution boundary for Android playback-audio capture.
 *
 * <p>The service owns the one-shot {@link MediaProjection}, {@link AudioRecord},
 * capture thread, and safety limits. A locally bound Capacitor plugin supplies
 * the user-approved projection result and receives bounded PCM chunks. The
 * projection token and captured audio are never persisted.</p>
 */
public final class PlaybackAudioCaptureService extends Service {
    public static final String ACTION_PREPARE = "com.ananta.mobile.voice.PLAYBACK_CAPTURE_PREPARE";
    public static final String ACTION_STOP = "com.ananta.mobile.voice.PLAYBACK_CAPTURE_STOP";

    private static final String NOTIFICATION_CHANNEL_ID = "ananta_playback_capture";
    private static final int NOTIFICATION_ID = 9_031;
    private static final int SAMPLE_RATE = 16_000;
    private static final int CHANNELS = 1;
    private static final int BYTES_PER_SAMPLE = 2;
    private static final int MIN_CHUNK_MILLISECONDS = 100;
    private static final int MAX_CHUNK_MILLISECONDS = 1_000;
    private static final int DEFAULT_CHUNK_MILLISECONDS = 500;
    private static final int DEFAULT_MAX_SECONDS = 120;
    private static final int MAX_SESSION_SECONDS = 28_800;
    private static final long BYTES_PER_SECOND = (long) SAMPLE_RATE * BYTES_PER_SAMPLE;

    private enum CaptureLifecycle {
        PREPARED,
        STARTING,
        CAPTURING,
        STOPPING,
        STOPPED,
        DESTROYED
    }

    /** Local-only callback. The service is non-exported and runs in the app process. */
    public interface Listener {
        void onChunk(CaptureChunk chunk);

        void onError(String code, String message);

        void onStopped(CaptureSummary summary);
    }

    public static final class CaptureChunk {
        public final int sequence;
        public final byte[] pcm;
        public final long capturedBytes;

        CaptureChunk(int sequence, byte[] pcm, long capturedBytes) {
            this.sequence = sequence;
            this.pcm = pcm;
            this.capturedBytes = capturedBytes;
        }

        public long capturedMilliseconds() {
            return capturedBytes * 1_000L / BYTES_PER_SECOND;
        }
    }

    public static final class CaptureSummary {
        public final boolean capturing;
        public final boolean stopped;
        public final int nextSequence;
        public final long capturedBytes;
        public final String reason;

        CaptureSummary(
                boolean capturing,
                boolean stopped,
                int nextSequence,
                long capturedBytes,
                String reason
        ) {
            this.capturing = capturing;
            this.stopped = stopped;
            this.nextSequence = nextSequence;
            this.capturedBytes = capturedBytes;
            this.reason = reason;
        }

        public long capturedMilliseconds() {
            return capturedBytes * 1_000L / BYTES_PER_SECOND;
        }
    }

    public static final class CaptureException extends Exception {
        public final String code;

        CaptureException(String code, String message) {
            super(message);
            this.code = code;
        }

        CaptureException(String code, String message, Throwable cause) {
            super(message, cause);
            this.code = code;
        }
    }

    /** Binder is intentionally local; no process-global or exported state is used. */
    public final class LocalBinder extends Binder {
        public void setListener(@Nullable Listener value) {
            synchronized (stateLock) {
                listener = value;
            }
        }

        public void clearListener(Listener expected) {
            synchronized (stateLock) {
                if (listener == expected) listener = null;
            }
        }

        public CaptureSummary startCapture(
                int resultCode,
                Intent resultData,
                int chunkMilliseconds,
                int maxSeconds
        ) throws CaptureException {
            return PlaybackAudioCaptureService.this.startCapture(
                    resultCode,
                    resultData,
                    chunkMilliseconds,
                    maxSeconds
            );
        }

        public CaptureSummary stopCapture(String reason) {
            return PlaybackAudioCaptureService.this.stopCaptureAndWait(normalizedReason(reason));
        }

        public CaptureSummary getStatus() {
            synchronized (stateLock) {
                return summaryLocked(captureThread == null || !captureThread.isAlive());
            }
        }
    }

    private final Object stateLock = new Object();
    private final LocalBinder localBinder = new LocalBinder();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Nullable
    private Listener listener;
    @Nullable
    private AudioRecord recorder;
    @Nullable
    private Thread captureThread;
    @Nullable
    private MediaProjection mediaProjection;
    @Nullable
    private MediaProjection.Callback projectionCallback;
    private volatile boolean capturing;
    private int nextSequence;
    private long capturedBytes;
    private String stopReason = "stopped";
    private boolean stoppedEventSent;
    private boolean foregroundStarted;
    private boolean serviceFinishing;
    private CaptureLifecycle lifecycleState = CaptureLifecycle.PREPARED;
    private long lifecycleGeneration;
    private long activeCaptureGeneration = -1L;

    @Override
    public void onCreate() {
        super.onCreate();
        synchronized (stateLock) {
            lifecycleState = CaptureLifecycle.PREPARED;
            lifecycleGeneration += 1L;
            activeCaptureGeneration = -1L;
            serviceFinishing = false;
        }
        // This service is only started after a successful projection consent result.
        ensureForeground();
    }

    @Override
    public int onStartCommand(@Nullable Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            requestStop("notification_stop", true);
        } else if (mayRemainForeground()) {
            ensureForeground();
        }
        return START_NOT_STICKY;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        if (mayRemainForeground()) ensureForeground();
        return localBinder;
    }

    @Override
    public boolean onUnbind(Intent intent) {
        requestStop("client_unbound", true);
        return false;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        requestStop("task_removed", true);
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onDestroy() {
        requestStop("service_destroyed", true);
        synchronized (stateLock) {
            lifecycleState = CaptureLifecycle.DESTROYED;
            lifecycleGeneration += 1L;
            capturing = false;
        }
        removeForegroundNotification();
        super.onDestroy();
    }

    private CaptureSummary startCapture(
            int resultCode,
            Intent resultData,
            int requestedChunkMilliseconds,
            int requestedMaxSeconds
    ) throws CaptureException {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            throw new CaptureException(
                    "PLAYBACK_CAPTURE_UNSUPPORTED",
                    "Playback audio capture requires Android 10 or newer."
            );
        }
        if (resultCode != Activity.RESULT_OK || resultData == null) {
            throw new CaptureException(
                    "PLAYBACK_CAPTURE_CONSENT_INVALID",
                    "A valid playback capture consent result is required."
            );
        }
        final long startGeneration;
        synchronized (stateLock) {
            if (
                    lifecycleState != CaptureLifecycle.PREPARED
                            || serviceFinishing
                            || capturing
                            || (captureThread != null && captureThread.isAlive())
            ) {
                String code = lifecycleState == CaptureLifecycle.STOPPING
                                || lifecycleState == CaptureLifecycle.STOPPED
                                || lifecycleState == CaptureLifecycle.DESTROYED
                        ? "PLAYBACK_CAPTURE_START_CANCELLED"
                        : "PLAYBACK_CAPTURE_ALREADY_RUNNING";
                throw new CaptureException(
                        code,
                        "Playback audio capture was stopped or is already running."
                );
            }
            lifecycleState = CaptureLifecycle.STARTING;
            startGeneration = ++lifecycleGeneration;
            stopReason = "stopped";
            stoppedEventSent = false;
        }

        int chunkMilliseconds = bounded(
                requestedChunkMilliseconds,
                DEFAULT_CHUNK_MILLISECONDS,
                MIN_CHUNK_MILLISECONDS,
                MAX_CHUNK_MILLISECONDS
        );
        int maxSeconds = boundedMaxSeconds(requestedMaxSeconds);
        int minimumBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
        );
        if (minimumBuffer <= 0) {
            failStartingGeneration(startGeneration, "format_unsupported");
            finishServiceSoon();
            throw new CaptureException(
                    "PLAYBACK_CAPTURE_FORMAT_UNSUPPORTED",
                    "The device does not support 16000 Hz mono PCM playback capture."
            );
        }
        int chunkBytes = evenBytes(SAMPLE_RATE * BYTES_PER_SAMPLE * chunkMilliseconds / 1_000);
        int recorderBuffer = Math.max(minimumBuffer * 2, chunkBytes * 2);

        MediaProjection projection = null;
        MediaProjection.Callback callback = null;
        AudioRecord nextRecorder = null;
        try {
            MediaProjectionManager manager = getSystemService(MediaProjectionManager.class);
            if (manager == null) {
                throw new CaptureException(
                        "PLAYBACK_CAPTURE_SERVICE_UNAVAILABLE",
                        "Android MediaProjection service is unavailable."
                );
            }
            // Target SDK 35 requires this call to run after startForeground().
            projection = manager.getMediaProjection(resultCode, resultData);
            if (projection == null) {
                throw new CaptureException(
                        "PLAYBACK_CAPTURE_CONSENT_INVALID",
                        "Android did not return a playback capture projection."
                );
            }
            ProjectionStopCallback stopCallback = new ProjectionStopCallback(projection, startGeneration);
            callback = stopCallback;
            projection.registerCallback(callback, mainHandler);

            AudioPlaybackCaptureConfiguration captureConfiguration =
                    new AudioPlaybackCaptureConfiguration.Builder(projection)
                            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                            .addMatchingUsage(AudioAttributes.USAGE_GAME)
                            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
                            .build();
            AudioFormat format = new AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .build();
            // setAudioSource() must not be combined with playback capture configuration.
            nextRecorder = new AudioRecord.Builder()
                    .setAudioFormat(format)
                    .setBufferSizeInBytes(recorderBuffer)
                    .setAudioPlaybackCaptureConfig(captureConfiguration)
                    .build();
            if (nextRecorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new CaptureException(
                        "PLAYBACK_CAPTURE_INITIALIZATION_FAILED",
                        "Playback AudioRecord could not be initialized."
                );
            }

            final AudioRecord activeRecorder = nextRecorder;
            final MediaProjection activeProjection = projection;
            final MediaProjection.Callback activeCallback = callback;
            synchronized (stateLock) {
                if (
                        lifecycleState != CaptureLifecycle.STARTING
                                || lifecycleGeneration != startGeneration
                                || serviceFinishing
                ) {
                    throw new CaptureException(
                            "PLAYBACK_CAPTURE_START_CANCELLED",
                            "Playback audio capture was stopped while it was starting."
                    );
                }
                recorder = activeRecorder;
                mediaProjection = activeProjection;
                projectionCallback = activeCallback;
                capturing = true;
                lifecycleState = CaptureLifecycle.CAPTURING;
                activeCaptureGeneration = startGeneration;
                nextSequence = 0;
                capturedBytes = 0;
                stopReason = "stopped";
                stoppedEventSent = false;
                captureThread = new Thread(
                        () -> captureLoop(activeRecorder, chunkBytes, maxSeconds, startGeneration),
                        "ananta-playback-audio-capture"
                );
                captureThread.start();
                return summaryLocked(false);
            }
        } catch (CaptureException error) {
            clearFailedSetupState(startGeneration, nextRecorder, projection);
            releaseSetupResources(nextRecorder, projection, callback);
            finishServiceSoon();
            throw error;
        } catch (SecurityException error) {
            clearFailedSetupState(startGeneration, nextRecorder, projection);
            releaseSetupResources(nextRecorder, projection, callback);
            finishServiceSoon();
            throw new CaptureException(
                    "PLAYBACK_CAPTURE_SECURITY_ERROR",
                    "Android rejected the playback capture session.",
                    error
            );
        } catch (RuntimeException error) {
            clearFailedSetupState(startGeneration, nextRecorder, projection);
            releaseSetupResources(nextRecorder, projection, callback);
            finishServiceSoon();
            throw new CaptureException(
                    "PLAYBACK_CAPTURE_INITIALIZATION_FAILED",
                    "Playback audio capture could not be initialized on this device.",
                    error
            );
        }
    }

    private void captureLoop(
            AudioRecord activeRecorder,
            int chunkBytes,
            int maxSeconds,
            long captureGeneration
    ) {
        long maxBytes = maximumCaptureBytes(maxSeconds);
        try {
            activeRecorder.startRecording();
            while (capturing && capturedBytesSnapshot() < maxBytes) {
                long alreadyCaptured = capturedBytesSnapshot();
                int remainingBudget = (int) Math.min(chunkBytes, maxBytes - alreadyCaptured);
                byte[] chunk = new byte[evenBytes(remainingBudget)];
                int offset = 0;
                while (capturing && offset < chunk.length) {
                    int read = activeRecorder.read(chunk, offset, chunk.length - offset);
                    if (read < 0) {
                        throw new IllegalStateException("Playback AudioRecord read failed: " + read);
                    }
                    if (read == 0) continue;
                    offset += read;
                }
                int accepted = evenBytes(offset);
                if (accepted > 0) emitChunk(chunk, accepted, captureGeneration);
            }
            if (capturedBytesSnapshot() >= maxBytes) setStopReason("safety_limit");
        } catch (RuntimeException error) {
            if (capturing) {
                setStopReason("capture_error");
                emitError(
                        "PLAYBACK_CAPTURE_READ_FAILED",
                        messageOrFallback(error, "Playback audio capture failed.")
                );
            }
        } finally {
            capturing = false;
            stopRecorderQuietly(activeRecorder);
            activeRecorder.release();
            finishCapture(activeRecorder, captureGeneration);
        }
    }

    private void emitChunk(byte[] chunk, int length, long captureGeneration) {
        byte[] payload;
        if (length == chunk.length) {
            payload = chunk;
        } else {
            payload = new byte[length];
            System.arraycopy(chunk, 0, payload, 0, length);
        }
        CaptureChunk event;
        Listener target;
        synchronized (stateLock) {
            if (
                    (lifecycleState != CaptureLifecycle.CAPTURING
                            && lifecycleState != CaptureLifecycle.STOPPING)
                            || activeCaptureGeneration != captureGeneration
            ) {
                return;
            }
            int sequence = nextSequence++;
            capturedBytes += payload.length;
            event = new CaptureChunk(sequence, payload, capturedBytes);
            target = listener;
        }
        if (target != null) mainHandler.post(() -> target.onChunk(event));
    }

    private void emitError(String code, String message) {
        Listener target;
        synchronized (stateLock) {
            target = listener;
        }
        if (target != null) mainHandler.post(() -> target.onError(code, message));
    }

    private void finishCapture(AudioRecord activeRecorder, long captureGeneration) {
        MediaProjection projectionToStop;
        MediaProjection.Callback callbackToRemove;
        Listener target;
        CaptureSummary summary;
        synchronized (stateLock) {
            if (activeCaptureGeneration != captureGeneration || recorder != activeRecorder) return;
            if (recorder == activeRecorder) recorder = null;
            if (captureThread == Thread.currentThread()) captureThread = null;
            capturing = false;
            activeCaptureGeneration = -1L;
            if (lifecycleState != CaptureLifecycle.DESTROYED) {
                lifecycleState = CaptureLifecycle.STOPPED;
            }
            projectionToStop = mediaProjection;
            callbackToRemove = projectionCallback;
            mediaProjection = null;
            projectionCallback = null;
            target = listener;
            summary = summaryLocked(true);
            if (stoppedEventSent) target = null;
            stoppedEventSent = true;
        }
        stopProjectionQuietly(projectionToStop, callbackToRemove);
        Listener stoppedTarget = target;
        if (stoppedTarget != null) mainHandler.post(() -> stoppedTarget.onStopped(summary));
        finishServiceSoon();
    }

    private CaptureSummary stopCaptureAndWait(String reason) {
        Thread thread = requestStop(reason, true);
        if (thread != null && thread != Thread.currentThread()) {
            try {
                thread.join(2_500L);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
        }
        synchronized (stateLock) {
            return summaryLocked(thread == null || !thread.isAlive());
        }
    }

    @Nullable
    private Thread requestStop(String reason, boolean stopProjection) {
        AudioRecord recorderToStop = null;
        MediaProjection projectionToStop = null;
        MediaProjection.Callback callbackToRemove = null;
        Thread thread = null;
        Listener immediateTarget = null;
        CaptureSummary immediateSummary = null;
        boolean finishImmediately = false;
        synchronized (stateLock) {
            if (
                    lifecycleState == CaptureLifecycle.STOPPED
                            || lifecycleState == CaptureLifecycle.DESTROYED
            ) {
                finishImmediately = true;
            } else {
                stopReason = normalizedReason(reason);
                lifecycleState = CaptureLifecycle.STOPPING;
                lifecycleGeneration += 1L;
                capturing = false;
                recorderToStop = recorder;
                thread = captureThread;
                if (stopProjection) {
                    projectionToStop = mediaProjection;
                    callbackToRemove = projectionCallback;
                    mediaProjection = null;
                    projectionCallback = null;
                }
                if (thread == null) {
                    activeCaptureGeneration = -1L;
                    lifecycleState = CaptureLifecycle.STOPPED;
                    immediateTarget = listener;
                    immediateSummary = summaryLocked(true);
                    if (stoppedEventSent) immediateTarget = null;
                    stoppedEventSent = true;
                    finishImmediately = true;
                }
            }
        }
        stopRecorderQuietly(recorderToStop);
        if (stopProjection) stopProjectionQuietly(projectionToStop, callbackToRemove);
        Listener stoppedTarget = immediateTarget;
        CaptureSummary stoppedSummary = immediateSummary;
        if (stoppedTarget != null && stoppedSummary != null) {
            mainHandler.post(() -> stoppedTarget.onStopped(stoppedSummary));
        }
        if (finishImmediately) finishServiceSoon();
        return thread;
    }

    private void handleProjectionStopped(MediaProjection stoppedProjection, long captureGeneration) {
        boolean relevant;
        synchronized (stateLock) {
            relevant = (
                    lifecycleState == CaptureLifecycle.STARTING
                            && lifecycleGeneration == captureGeneration
            ) || (
                    activeCaptureGeneration == captureGeneration
                            && mediaProjection == stoppedProjection
            );
            if (!relevant) return;
            if (mediaProjection == stoppedProjection) {
                mediaProjection = null;
                projectionCallback = null;
            }
        }
        requestStop("projection_revoked", false);
    }

    private void clearFailedSetupState(
            long startGeneration,
            @Nullable AudioRecord failedRecorder,
            @Nullable MediaProjection failedProjection
    ) {
        synchronized (stateLock) {
            boolean ownedActiveCapture = activeCaptureGeneration == startGeneration;
            if (recorder == failedRecorder) recorder = null;
            if (mediaProjection == failedProjection) {
                mediaProjection = null;
                projectionCallback = null;
            }
            if (captureThread != null && !captureThread.isAlive()) captureThread = null;
            if (ownedActiveCapture) activeCaptureGeneration = -1L;
            if (
                    (lifecycleState == CaptureLifecycle.STARTING
                            && lifecycleGeneration == startGeneration)
                            || (lifecycleState == CaptureLifecycle.CAPTURING && ownedActiveCapture)
            ) {
                lifecycleState = CaptureLifecycle.STOPPED;
                lifecycleGeneration += 1L;
                stopReason = "initialization_failed";
            }
            if (lifecycleState != CaptureLifecycle.CAPTURING) capturing = false;
        }
    }

    private void failStartingGeneration(long startGeneration, String reason) {
        synchronized (stateLock) {
            if (
                    lifecycleState == CaptureLifecycle.STARTING
                            && lifecycleGeneration == startGeneration
            ) {
                lifecycleState = CaptureLifecycle.STOPPED;
                lifecycleGeneration += 1L;
                stopReason = normalizedReason(reason);
                capturing = false;
            }
        }
    }

    private final class ProjectionStopCallback extends MediaProjection.Callback {
        private final MediaProjection expectedProjection;
        private final long captureGeneration;

        ProjectionStopCallback(MediaProjection expectedProjection, long captureGeneration) {
            this.expectedProjection = expectedProjection;
            this.captureGeneration = captureGeneration;
        }

        @Override
        public void onStop() {
            handleProjectionStopped(expectedProjection, captureGeneration);
        }
    }

    private boolean mayRemainForeground() {
        synchronized (stateLock) {
            return lifecycleState == CaptureLifecycle.PREPARED
                    || lifecycleState == CaptureLifecycle.STARTING
                    || lifecycleState == CaptureLifecycle.CAPTURING;
        }
    }

    private void ensureForeground() {
        if (foregroundStarted) return;
        createNotificationChannel();
        Notification notification = createNotification();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            );
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
        foregroundStarted = true;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                getString(R.string.playback_capture_channel_name),
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription(getString(R.string.playback_capture_channel_description));
        manager.createNotificationChannel(channel);
    }

    private Notification createNotification() {
        Intent openIntent = new Intent(this, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent openPendingIntent = PendingIntent.getActivity(
                this,
                0,
                openIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        Intent stopIntent = new Intent(this, PlaybackAudioCaptureService.class)
                .setAction(ACTION_STOP);
        PendingIntent stopPendingIntent = PendingIntent.getService(
                this,
                1,
                stopIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle(getString(R.string.playback_capture_notification_title))
                .setContentText(getString(R.string.playback_capture_notification_text))
                .setContentIntent(openPendingIntent)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .addAction(
                        new Notification.Action.Builder(
                                null,
                                getString(R.string.playback_capture_notification_stop),
                                stopPendingIntent
                        ).build()
                )
                .build();
    }

    private void finishServiceSoon() {
        synchronized (stateLock) {
            if (serviceFinishing) return;
            serviceFinishing = true;
        }
        mainHandler.post(() -> {
            removeForegroundNotification();
            stopSelf();
        });
    }

    private void removeForegroundNotification() {
        if (!foregroundStarted) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE);
        } else {
            stopForeground(true);
        }
        foregroundStarted = false;
    }

    private CaptureSummary summaryLocked(boolean stopped) {
        return new CaptureSummary(
                capturing,
                stopped,
                nextSequence,
                capturedBytes,
                stopReason
        );
    }

    private long capturedBytesSnapshot() {
        synchronized (stateLock) {
            return capturedBytes;
        }
    }

    private void setStopReason(String reason) {
        synchronized (stateLock) {
            stopReason = normalizedReason(reason);
        }
    }

    private static int bounded(int requested, int defaultValue, int minimum, int maximum) {
        int value = requested <= 0 ? defaultValue : requested;
        return Math.max(minimum, Math.min(maximum, value));
    }

    static int boundedMaxSeconds(int requested) {
        return bounded(requested, DEFAULT_MAX_SECONDS, 1, MAX_SESSION_SECONDS);
    }

    static long maximumCaptureBytes(int maxSeconds) {
        return BYTES_PER_SECOND * (long) maxSeconds;
    }

    private static int evenBytes(int value) {
        return Math.max(0, value - (value % BYTES_PER_SAMPLE));
    }

    private static String normalizedReason(@Nullable String reason) {
        if (reason == null || reason.isBlank()) return "stopped";
        return reason;
    }

    private static String messageOrFallback(Throwable error, String fallback) {
        String message = error.getMessage();
        return message == null || message.isBlank() ? fallback : message;
    }

    private static void stopRecorderQuietly(@Nullable AudioRecord value) {
        if (value == null) return;
        try {
            if (value.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) value.stop();
        } catch (RuntimeException ignored) {
            // Stopping is used to unblock a pending read; the capture thread releases the recorder.
        }
    }

    private static void stopProjectionQuietly(
            @Nullable MediaProjection projection,
            @Nullable MediaProjection.Callback callback
    ) {
        if (projection == null) return;
        if (callback != null) {
            try {
                projection.unregisterCallback(callback);
            } catch (RuntimeException ignored) {
                // Projection may already have been revoked by Android.
            }
        }
        try {
            projection.stop();
        } catch (RuntimeException ignored) {
            // Projection stop is idempotent from the service's perspective.
        }
    }

    private static void releaseSetupResources(
            @Nullable AudioRecord recorder,
            @Nullable MediaProjection projection,
            @Nullable MediaProjection.Callback callback
    ) {
        if (recorder != null) {
            stopRecorderQuietly(recorder);
            try {
                recorder.release();
            } catch (RuntimeException ignored) {
                // A partially initialized recorder may already be released.
            }
        }
        stopProjectionQuietly(projection, callback);
    }
}
