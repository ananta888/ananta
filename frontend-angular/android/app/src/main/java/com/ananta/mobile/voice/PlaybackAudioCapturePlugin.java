package com.ananta.mobile.voice;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;

import androidx.activity.result.ActivityResult;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Capacitor facade for user-consented Android playback-audio capture.
 *
 * <p>The plugin owns only permission/consent and bridge translation. The bound
 * {@link PlaybackAudioCaptureService} owns MediaProjection, AudioRecord, and
 * capture lifecycle. Recognition and model selection remain Hub-owned.</p>
 */
@CapacitorPlugin(
        name = "PlaybackAudioCapture",
        permissions = {
                @Permission(strings = {Manifest.permission.RECORD_AUDIO}, alias = "recordAudio")
        }
)
public final class PlaybackAudioCapturePlugin extends Plugin {
    private static final int SAMPLE_RATE = 16_000;
    private static final int DEFAULT_CHUNK_MILLISECONDS = 500;
    private static final int DEFAULT_MAX_SECONDS = 120;
    private static final int MAX_SESSION_SECONDS = 28_800;
    private static final long SERVICE_BIND_TIMEOUT_MILLISECONDS = 5_000L;
    private static final long PREPARED_CONSENT_TIMEOUT_MILLISECONDS = 60_000L;

    private enum PluginLifecycle {
        IDLE,
        PREPARING,
        PREPARED,
        STARTING,
        CAPTURING,
        STOPPING,
        DESTROYED
    }

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Object lifecycleLock = new Object();
    private boolean consentPending;
    private boolean discardConsentResult;
    private boolean bindingRequested;
    private boolean serviceBound;
    private PluginLifecycle lifecycleState = PluginLifecycle.IDLE;
    private long lifecycleGeneration;
    private long preparingGeneration = -1L;
    private long bindingGeneration = -1L;
    @Nullable
    private PluginCall preparingCall;
    @Nullable
    private PreparedProjection preparedProjection;
    @Nullable
    private PendingStart pendingStart;
    @Nullable
    private PendingStop pendingStop;
    @Nullable
    private PlaybackAudioCaptureService.LocalBinder captureBinder;
    @Nullable
    private PlaybackAudioCaptureService.Listener captureListener;
    @Nullable
    private ServiceConnection serviceConnection;

    private final Runnable serviceBindTimeout = () -> {
        synchronized (lifecycleLock) {
            if (
                    pendingStart == null
                            || serviceBound
                            || lifecycleState != PluginLifecycle.STARTING
                            || pendingStart.generation != lifecycleGeneration
            ) {
                return;
            }
        }
        failPendingStart(
                "PLAYBACK_CAPTURE_SERVICE_TIMEOUT",
                "Timed out while connecting to the playback capture service.",
                null
        );
    };

    private final Runnable preparedConsentTimeout = () -> {
        synchronized (lifecycleLock) {
            if (
                    lifecycleState != PluginLifecycle.PREPARED
                            || preparedProjection == null
                            || preparedProjection.generation != lifecycleGeneration
            ) {
                return;
            }
            preparedProjection = null;
            lifecycleState = PluginLifecycle.IDLE;
            lifecycleGeneration += 1L;
        }
    };

    @PluginMethod
    public void getStatus(PluginCall call) {
        discardExpiredPreparedProjection();
        PlaybackAudioCaptureService.LocalBinder binder;
        boolean prepared;
        boolean starting;
        synchronized (lifecycleLock) {
            binder = captureBinder;
            prepared = lifecycleState == PluginLifecycle.PREPARED && preparedProjection != null;
            starting = lifecycleState == PluginLifecycle.PREPARING
                    || lifecycleState == PluginLifecycle.STARTING;
        }
        PlaybackAudioCaptureService.CaptureSummary summary = null;
        if (binder != null) {
            try {
                summary = binder.getStatus();
            } catch (RuntimeException ignored) {
                // A disconnected service is reported as inactive below.
            }
        }
        JSObject result = new JSObject();
        result.put("source", "playback");
        result.put("supported", Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q);
        result.put("minimumAndroidApi", Build.VERSION_CODES.Q);
        result.put("projectionConsentRequired", true);
        result.put("recordAudioPermission", permissionValue());
        result.put("prepared", prepared);
        result.put("starting", starting);
        result.put("capturing", summary != null && summary.capturing);
        result.put("nextSequence", summary == null ? 0 : summary.nextSequence);
        result.put("capturedBytes", summary == null ? 0 : summary.capturedBytes);
        result.put("capturedMilliseconds", summary == null ? 0 : summary.capturedMilliseconds());
        putMediaContract(result);
        call.resolve(result);
    }

    @PluginMethod
    public void requestAudioPermission(PluginCall call) {
        if (getPermissionState("recordAudio") == PermissionState.GRANTED) {
            JSObject result = new JSObject();
            result.put("state", "granted");
            call.resolve(result);
            return;
        }
        requestPermissionForAlias("recordAudio", call, "audioPermissionResult");
    }

    @SuppressWarnings("unused")
    @PermissionCallback
    public void audioPermissionResult(PluginCall call) {
        JSObject result = new JSObject();
        result.put("state", permissionValue());
        call.resolve(result);
    }

    /**
     * Requests all user-controlled prerequisites from a direct UI gesture.
     * The approved projection result is held in memory for a short, bounded
     * window and may be consumed by exactly one {@link #start(PluginCall)}.
     */
    @PluginMethod
    public void prepare(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            call.reject(
                    "Playback audio capture requires Android 10 or newer.",
                    "PLAYBACK_CAPTURE_UNSUPPORTED"
            );
            return;
        }
        discardExpiredPreparedProjection();
        synchronized (lifecycleLock) {
            if (lifecycleState != PluginLifecycle.IDLE || consentPending) {
                call.reject(
                        "Playback audio capture is already prepared, running, or awaiting consent.",
                        "PLAYBACK_CAPTURE_PREPARE_ALREADY_ACTIVE"
                );
                return;
            }
            lifecycleState = PluginLifecycle.PREPARING;
            preparingGeneration = ++lifecycleGeneration;
            preparingCall = call;
            discardConsentResult = false;
            consentPending = true;
        }
        if (getPermissionState("recordAudio") != PermissionState.GRANTED) {
            requestPermissionForAlias("recordAudio", call, "prepareAudioPermissionResult");
            return;
        }
        launchProjectionConsent(call);
    }

    @SuppressWarnings("unused")
    @PermissionCallback
    public void prepareAudioPermissionResult(PluginCall call) {
        synchronized (lifecycleLock) {
            if (!isCurrentPreparationLocked(call) || discardConsentResult) {
                clearPreparationLocked();
                call.reject(
                        "Playback capture preparation was cancelled.",
                        "PLAYBACK_CAPTURE_PREPARE_CANCELLED"
                );
                return;
            }
        }
        if (getPermissionState("recordAudio") != PermissionState.GRANTED) {
            synchronized (lifecycleLock) {
                clearPreparationLocked();
                if (lifecycleState == PluginLifecycle.PREPARING) lifecycleState = PluginLifecycle.IDLE;
            }
            call.reject(
                    "Record audio permission is required for playback capture.",
                    "PLAYBACK_CAPTURE_RECORD_AUDIO_PERMISSION_REQUIRED"
            );
            return;
        }
        launchProjectionConsent(call);
    }

    private void launchProjectionConsent(PluginCall call) {
        synchronized (lifecycleLock) {
            if (!isCurrentPreparationLocked(call)) {
                if (preparingCall == call) clearPreparationLocked();
                call.reject(
                        "Playback capture preparation was cancelled.",
                        "PLAYBACK_CAPTURE_PREPARE_CANCELLED"
                );
                return;
            }
        }
        MediaProjectionManager manager = getContext().getSystemService(MediaProjectionManager.class);
        if (manager == null) {
            synchronized (lifecycleLock) {
                clearPreparationLocked();
                if (lifecycleState == PluginLifecycle.PREPARING) lifecycleState = PluginLifecycle.IDLE;
            }
            call.reject(
                    "Android MediaProjection service is unavailable.",
                    "PLAYBACK_CAPTURE_SERVICE_UNAVAILABLE"
            );
            return;
        }
        try {
            startActivityForResult(
                    call,
                    manager.createScreenCaptureIntent(),
                    "projectionConsentResult"
            );
        } catch (RuntimeException error) {
            synchronized (lifecycleLock) {
                clearPreparationLocked();
                if (lifecycleState == PluginLifecycle.PREPARING) lifecycleState = PluginLifecycle.IDLE;
            }
            call.reject(
                    "Playback capture consent dialog could not be opened.",
                    "PLAYBACK_CAPTURE_CONSENT_LAUNCH_FAILED",
                    error
            );
        }
    }

    @SuppressWarnings("unused")
    @ActivityCallback
    private void projectionConsentResult(@Nullable PluginCall call, ActivityResult result) {
        if (call == null) return;
        final long preparationGeneration;
        synchronized (lifecycleLock) {
            if (!isCurrentPreparationLocked(call) || discardConsentResult) {
                clearPreparationLocked();
                call.reject(
                        "Playback capture preparation was cancelled.",
                        "PLAYBACK_CAPTURE_PREPARE_CANCELLED"
                );
                return;
            }
            preparationGeneration = preparingGeneration;
            consentPending = false;
            preparingCall = null;
        }
        Intent resultData = result.getData();
        if (result.getResultCode() != Activity.RESULT_OK || resultData == null) {
            synchronized (lifecycleLock) {
                if (
                        lifecycleState == PluginLifecycle.PREPARING
                                && lifecycleGeneration == preparationGeneration
                ) {
                    lifecycleState = PluginLifecycle.IDLE;
                    preparingGeneration = -1L;
                }
            }
            call.reject(
                    "Playback capture permission was not granted.",
                    "PLAYBACK_CAPTURE_CONSENT_DENIED"
            );
            return;
        }
        synchronized (lifecycleLock) {
            if (
                    lifecycleState != PluginLifecycle.PREPARING
                            || lifecycleGeneration != preparationGeneration
            ) {
                preparingGeneration = -1L;
                call.reject(
                        "Playback capture preparation was cancelled.",
                        "PLAYBACK_CAPTURE_PREPARE_CANCELLED"
                );
                return;
            }
            preparedProjection = new PreparedProjection(
                    result.getResultCode(),
                    new Intent(resultData),
                    SystemClock.elapsedRealtime() + PREPARED_CONSENT_TIMEOUT_MILLISECONDS,
                    preparationGeneration
            );
            lifecycleState = PluginLifecycle.PREPARED;
            preparingGeneration = -1L;
        }
        mainHandler.removeCallbacks(preparedConsentTimeout);
        mainHandler.postDelayed(preparedConsentTimeout, PREPARED_CONSENT_TIMEOUT_MILLISECONDS);
        JSObject prepared = new JSObject();
        prepared.put("source", "playback");
        prepared.put("prepared", true);
        prepared.put("consentExpiresInMilliseconds", PREPARED_CONSENT_TIMEOUT_MILLISECONDS);
        putMediaContract(prepared);
        call.resolve(prepared);
    }
    /** Consumes one fresh prepare() result and starts the foreground capture service. */
    @PluginMethod
    public void start(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            call.reject(
                    "Playback audio capture requires Android 10 or newer.",
                    "PLAYBACK_CAPTURE_UNSUPPORTED"
            );
            return;
        }
        if (getPermissionState("recordAudio") != PermissionState.GRANTED) {
            clearPreparedProjection();
            call.reject(
                    "Record audio permission is required for playback capture.",
                    "PLAYBACK_CAPTURE_RECORD_AUDIO_PERMISSION_REQUIRED"
            );
            return;
        }
        discardExpiredPreparedProjection();
        final long startGeneration;
        final ServiceConnection connection;
        synchronized (lifecycleLock) {
            if (lifecycleState == PluginLifecycle.PREPARING || consentPending) {
                call.reject(
                        "Playback capture consent is still pending.",
                        "PLAYBACK_CAPTURE_CONSENT_PENDING"
                );
                return;
            }
            if (
                    lifecycleState != PluginLifecycle.PREPARED
                            || preparedProjection == null
                            || preparedProjection.generation != lifecycleGeneration
            ) {
                String code = lifecycleState == PluginLifecycle.STARTING
                                || lifecycleState == PluginLifecycle.CAPTURING
                                || lifecycleState == PluginLifecycle.STOPPING
                        ? "PLAYBACK_CAPTURE_ALREADY_RUNNING"
                        : "PLAYBACK_CAPTURE_PREPARE_REQUIRED";
                call.reject(
                        "Call prepare() from a user action before starting playback capture.",
                        code
                );
                return;
            }
            PreparedProjection prepared = preparedProjection;
            preparedProjection = null;
            mainHandler.removeCallbacks(preparedConsentTimeout);
            lifecycleState = PluginLifecycle.STARTING;
            startGeneration = ++lifecycleGeneration;
            bindingGeneration = startGeneration;
            pendingStart = new PendingStart(
                    call,
                    prepared.resultCode,
                    prepared.resultData,
                    call.getInt("chunkMilliseconds", DEFAULT_CHUNK_MILLISECONDS),
                    boundedMaxSeconds(call.getData().opt("maxSeconds")),
                    startGeneration
            );
            connection = new GenerationServiceConnection(startGeneration);
            serviceConnection = connection;
            bindingRequested = false;
            serviceBound = false;
            captureBinder = null;
            captureListener = null;
        }

        // Consent is complete. Start the mediaProjection foreground service
        // before its binder invokes MediaProjectionManager.getMediaProjection().
        Intent prepareIntent = new Intent(getContext(), PlaybackAudioCaptureService.class)
                .setAction(PlaybackAudioCaptureService.ACTION_PREPARE);
        try {
            synchronized (lifecycleLock) {
                if (!isPendingServiceLaunchLocked(startGeneration, connection)) return;
                // Keep launch atomic with the generation transition. A concurrent stop
                // therefore either invalidates this start before the platform call or
                // observes the already requested service and stops it afterwards.
                ContextCompat.startForegroundService(getContext(), prepareIntent);
            }
            synchronized (lifecycleLock) {
                if (!isPendingServiceLaunchLocked(startGeneration, connection)) return;
            }
            boolean bound = getContext().bindService(
                    new Intent(getContext(), PlaybackAudioCaptureService.class),
                    connection,
                    Context.BIND_AUTO_CREATE
            );
            boolean stale;
            boolean awaitingConnection;
            synchronized (lifecycleLock) {
                stale = lifecycleGeneration != startGeneration
                        || bindingGeneration != startGeneration
                        || serviceConnection != connection
                        || (lifecycleState != PluginLifecycle.STARTING
                        && lifecycleState != PluginLifecycle.CAPTURING);
                if (!stale) bindingRequested = bound;
                awaitingConnection = !stale
                        && lifecycleState == PluginLifecycle.STARTING
                        && !serviceBound;
            }
            if (stale) {
                boolean ownsBinding;
                synchronized (lifecycleLock) {
                    ownsBinding = bindingGeneration == startGeneration
                            && serviceConnection == connection;
                }
                if (ownsBinding) releaseServiceBinding(true, startGeneration);
                else if (bound) unbindConnectionQuietly(connection);
                return;
            }
            if (!bound) {
                failPendingStartForGeneration(
                        startGeneration,
                        "PLAYBACK_CAPTURE_SERVICE_BIND_FAILED",
                        "Playback capture service could not be bound.",
                        null
                );
                return;
            }
            if (awaitingConnection) {
                mainHandler.postDelayed(serviceBindTimeout, SERVICE_BIND_TIMEOUT_MILLISECONDS);
            }
        } catch (RuntimeException error) {
            failPendingStartForGeneration(
                    startGeneration,
                    "PLAYBACK_CAPTURE_SERVICE_START_FAILED",
                    "Playback capture foreground service could not be started.",
                    error
            );
        }
    }

    private boolean isPendingServiceLaunchLocked(
            long generation,
            ServiceConnection expectedConnection
    ) {
        return lifecycleState == PluginLifecycle.STARTING
                && lifecycleGeneration == generation
                && bindingGeneration == generation
                && serviceConnection == expectedConnection
                && pendingStart != null
                && pendingStart.generation == generation
                && !pendingStart.settled;
    }

    @PluginMethod
    public void stop(PluginCall call) {
        mainHandler.removeCallbacks(serviceBindTimeout);
        final long stoppedBindingGeneration;
        final PendingStart waiting;
        final PendingStop stopOperation;
        final PlaybackAudioCaptureService.LocalBinder binder;
        final boolean drainActiveCapture;
        synchronized (lifecycleLock) {
            if (pendingStop != null) {
                pendingStop.calls.add(call);
                return;
            }
            if (consentPending) discardConsentResult = true;
            mainHandler.removeCallbacks(preparedConsentTimeout);
            preparedProjection = null;
            waiting = pendingStart;
            pendingStart = null;
            if (waiting != null) waiting.settled = true;
            stoppedBindingGeneration = bindingGeneration;
            binder = captureBinder;
            drainActiveCapture = lifecycleState == PluginLifecycle.CAPTURING && binder != null;
            if (!drainActiveCapture) lifecycleGeneration += 1L;
            if (lifecycleState != PluginLifecycle.DESTROYED) {
                lifecycleState = PluginLifecycle.STOPPING;
            }
            stopOperation = new PendingStop(call, stoppedBindingGeneration);
            pendingStop = stopOperation;
        }
        if (waiting != null) {
            waiting.call.reject(
                    "Playback capture was stopped before it started.",
                    "PLAYBACK_CAPTURE_START_CANCELLED"
            );
        }

        PlaybackAudioCaptureService.CaptureSummary summary = null;
        try {
            if (binder != null) summary = binder.stopCapture("stopped");
        } catch (RuntimeException error) {
            releaseServiceBinding(true, stopOperation.generation);
            failStop(
                    stopOperation,
                    "Playback capture could not be stopped cleanly.",
                    "PLAYBACK_CAPTURE_STOP_FAILED",
                    error
            );
            return;
        }
        final PlaybackAudioCaptureService.CaptureSummary stoppedSummary = summary;
        if (drainActiveCapture) {
            // Capture chunks and the stopped event are queued on the main handler by the
            // service before stopCapture() returns. Posting completion here preserves that
            // order, so the final partial PCM chunk reaches JavaScript before stop resolves.
            mainHandler.post(() -> completeStop(
                    stopOperation,
                    stoppedSummary
            ));
            return;
        }
        completeStop(stopOperation, stoppedSummary);
    }

    private void completeStop(
            PendingStop operation,
            @Nullable PlaybackAudioCaptureService.CaptureSummary summary
    ) {
        releaseServiceBinding(true, operation.generation);
        final List<PluginCall> calls;
        synchronized (lifecycleLock) {
            if (lifecycleState == PluginLifecycle.STOPPING) lifecycleState = PluginLifecycle.IDLE;
            if (pendingStop != operation) return;
            pendingStop = null;
            calls = operation.takeCalls();
        }
        if (summary == null) {
            JSObject result = new JSObject();
            result.put("source", "playback");
            result.put("capturing", false);
            result.put("stopped", true);
            result.put("nextSequence", 0);
            result.put("capturedBytes", 0);
            result.put("capturedMilliseconds", 0);
            result.put("reason", "stopped");
            putMediaContract(result);
            for (PluginCall call : calls) call.resolve(result);
            return;
        }
        for (PluginCall call : calls) call.resolve(summaryResult(summary));
    }

    private void failStop(
            PendingStop operation,
            String message,
            String code,
            RuntimeException error
    ) {
        final List<PluginCall> calls;
        synchronized (lifecycleLock) {
            if (pendingStop != operation) return;
            pendingStop = null;
            if (lifecycleState == PluginLifecycle.STOPPING) lifecycleState = PluginLifecycle.IDLE;
            calls = operation.takeCalls();
        }
        for (PluginCall call : calls) call.reject(message, code, error);
    }

    @Override
    protected void handleOnDestroy() {
        mainHandler.removeCallbacks(serviceBindTimeout);
        mainHandler.removeCallbacks(preparedConsentTimeout);
        final PendingStart waiting;
        final PendingStop stopping;
        final long destroyedBindingGeneration;
        synchronized (lifecycleLock) {
            if (consentPending) discardConsentResult = true;
            preparedProjection = null;
            waiting = pendingStart;
            pendingStart = null;
            if (waiting != null) waiting.settled = true;
            stopping = pendingStop;
            pendingStop = null;
            destroyedBindingGeneration = bindingGeneration;
            lifecycleGeneration += 1L;
            lifecycleState = PluginLifecycle.DESTROYED;
        }
        if (waiting != null) {
            waiting.call.reject(
                    "Playback capture was cancelled because the Android bridge closed.",
                "PLAYBACK_CAPTURE_BRIDGE_DESTROYED"
            );
        }
        if (stopping != null) {
            for (PluginCall stopCall : stopping.takeCalls()) {
                stopCall.reject(
                        "Playback capture stop was cancelled because the Android bridge closed.",
                        "PLAYBACK_CAPTURE_BRIDGE_DESTROYED"
                );
            }
        }
        releaseServiceBinding(true, destroyedBindingGeneration);
        super.handleOnDestroy();
    }

    private void beginPendingCapture(
            long generation,
            PlaybackAudioCaptureService.LocalBinder binder,
            PlaybackAudioCaptureService.Listener expectedListener,
            ServiceConnection expectedConnection
    ) {
        final PendingStart start;
        synchronized (lifecycleLock) {
            start = isCurrentStartLocked(
                    generation,
                    binder,
                    expectedListener,
                    expectedConnection
            ) ? pendingStart : null;
        }
        if (start == null) {
            cancelStaleConnection(generation, binder, expectedListener, expectedConnection);
            return;
        }

        final PlaybackAudioCaptureService.CaptureSummary summary;
        try {
            summary = binder.startCapture(
                    start.resultCode,
                    start.resultData,
                    start.chunkMilliseconds,
                    start.maxSeconds
            );
        } catch (PlaybackAudioCaptureService.CaptureException error) {
            failPendingStartForGeneration(generation, error.code, error.getMessage(), error);
            return;
        } catch (RuntimeException error) {
            failPendingStartForGeneration(
                    generation,
                    "PLAYBACK_CAPTURE_INITIALIZATION_FAILED",
                    "Playback audio capture could not be initialized.",
                    error
            );
            return;
        }

        JSObject result = summaryResult(summary);
        result.put("started", true);
        result.put("chunkMilliseconds", boundedChunkMilliseconds(start.chunkMilliseconds));
        result.put("maxSeconds", boundedMaxSeconds(start.maxSeconds));
        boolean accepted;
        synchronized (lifecycleLock) {
            accepted = isCurrentStartLocked(
                    generation,
                    binder,
                    expectedListener,
                    expectedConnection
            ) && pendingStart == start && !start.settled;
            if (accepted) {
                pendingStart = null;
                start.settled = true;
                lifecycleState = PluginLifecycle.CAPTURING;
                // Settlement is part of the same lifecycle transition. A concurrent stop
                // therefore either cancels this call first or observes a completed start.
                start.call.resolve(result);
            }
        }
        if (!accepted) {
            cancelStaleConnection(generation, binder, expectedListener, expectedConnection);
        }
    }

    private boolean isCurrentStartLocked(
            long generation,
            PlaybackAudioCaptureService.LocalBinder binder,
            PlaybackAudioCaptureService.Listener expectedListener,
            ServiceConnection expectedConnection
    ) {
        return lifecycleState == PluginLifecycle.STARTING
                && lifecycleGeneration == generation
                && bindingGeneration == generation
                && pendingStart != null
                && pendingStart.generation == generation
                && !pendingStart.settled
                && captureBinder == binder
                && captureListener == expectedListener
                && serviceConnection == expectedConnection;
    }

    private void failPendingStart(String code, String message, @Nullable Exception error) {
        final long generation;
        synchronized (lifecycleLock) {
            if (pendingStart == null || pendingStart.settled) return;
            generation = pendingStart.generation;
        }
        failPendingStartForGeneration(generation, code, message, error);
    }

    private void failPendingStartForGeneration(
            long generation,
            String code,
            String message,
            @Nullable Exception error
    ) {
        mainHandler.removeCallbacks(serviceBindTimeout);
        final PendingStart failed;
        synchronized (lifecycleLock) {
            if (
                    pendingStart == null
                            || pendingStart.generation != generation
                            || pendingStart.settled
            ) {
                return;
            }
            failed = pendingStart;
            failed.settled = true;
            pendingStart = null;
            if (lifecycleGeneration == generation) lifecycleGeneration += 1L;
            if (lifecycleState == PluginLifecycle.STARTING) {
                lifecycleState = PluginLifecycle.STOPPING;
            }
        }
        if (error == null) failed.call.reject(message, code);
        else failed.call.reject(message, code, error);
        releaseServiceBinding(true, generation);
        synchronized (lifecycleLock) {
            if (lifecycleState == PluginLifecycle.STOPPING) lifecycleState = PluginLifecycle.IDLE;
        }
    }

    private void releaseServiceBinding(boolean terminateService, long expectedGeneration) {
        mainHandler.removeCallbacks(serviceBindTimeout);
        final PlaybackAudioCaptureService.LocalBinder binder;
        final PlaybackAudioCaptureService.Listener listener;
        final ServiceConnection connection;
        final boolean shouldUnbind;
        synchronized (lifecycleLock) {
            if (expectedGeneration >= 0L && bindingGeneration != expectedGeneration) return;
            binder = captureBinder;
            listener = captureListener;
            connection = serviceConnection;
            shouldUnbind = connection != null && (bindingRequested || serviceBound);
            captureBinder = null;
            captureListener = null;
            serviceConnection = null;
            bindingRequested = false;
            serviceBound = false;
            bindingGeneration = -1L;
        }
        if (binder != null && listener != null) binder.clearListener(listener);
        if (terminateService && binder != null) {
            try {
                binder.stopCapture("client_closed");
            } catch (RuntimeException ignored) {
                // Service destruction performs a second, idempotent cleanup attempt.
            }
        }
        if (shouldUnbind && connection != null) unbindConnectionQuietly(connection);
        if (terminateService) {
            getContext().stopService(new Intent(getContext(), PlaybackAudioCaptureService.class));
        }
    }

    private void cancelStaleConnection(
            long generation,
            PlaybackAudioCaptureService.LocalBinder binder,
            PlaybackAudioCaptureService.Listener expectedListener,
            ServiceConnection expectedConnection
    ) {
        final boolean stillOwnsBinding;
        synchronized (lifecycleLock) {
            stillOwnsBinding = bindingGeneration == generation
                    && serviceConnection == expectedConnection;
        }
        if (stillOwnsBinding) {
            releaseServiceBinding(true, generation);
            synchronized (lifecycleLock) {
                if (lifecycleState == PluginLifecycle.STOPPING) lifecycleState = PluginLifecycle.IDLE;
            }
            return;
        }
        // A newer generation may already own the service. Only detach this exact stale
        // callback; never stop resources which could belong to the newer generation.
        binder.clearListener(expectedListener);
        unbindConnectionQuietly(expectedConnection);
    }

    private void unbindConnectionQuietly(ServiceConnection connection) {
        try {
            getContext().unbindService(connection);
        } catch (IllegalArgumentException ignored) {
            // The platform may already have dropped a failed or stale binding.
        }
    }

    private boolean isCurrentPreparationLocked(PluginCall call) {
        return lifecycleState == PluginLifecycle.PREPARING
                && consentPending
                && preparingCall == call
                && preparingGeneration == lifecycleGeneration;
    }

    private void clearPreparationLocked() {
        consentPending = false;
        discardConsentResult = false;
        preparingCall = null;
        preparingGeneration = -1L;
        if (lifecycleState == PluginLifecycle.PREPARING) {
            lifecycleState = PluginLifecycle.IDLE;
            lifecycleGeneration += 1L;
        }
    }

    private void discardExpiredPreparedProjection() {
        boolean expired = false;
        synchronized (lifecycleLock) {
            if (
                    lifecycleState == PluginLifecycle.PREPARED
                            && preparedProjection != null
                            && SystemClock.elapsedRealtime()
                            >= preparedProjection.expiresAtElapsedRealtime
            ) {
                preparedProjection = null;
                lifecycleState = PluginLifecycle.IDLE;
                lifecycleGeneration += 1L;
                expired = true;
            }
        }
        if (expired) mainHandler.removeCallbacks(preparedConsentTimeout);
    }

    private void clearPreparedProjection() {
        mainHandler.removeCallbacks(preparedConsentTimeout);
        synchronized (lifecycleLock) {
            preparedProjection = null;
            if (lifecycleState == PluginLifecycle.PREPARED) {
                lifecycleState = PluginLifecycle.IDLE;
                lifecycleGeneration += 1L;
            }
        }
    }

    private final class GenerationListener implements PlaybackAudioCaptureService.Listener {
        private final long generation;

        GenerationListener(long generation) {
            this.generation = generation;
        }

        @Override
        public void onChunk(PlaybackAudioCaptureService.CaptureChunk chunk) {
            synchronized (lifecycleLock) {
                if (
                        (lifecycleState != PluginLifecycle.CAPTURING
                                && lifecycleState != PluginLifecycle.STOPPING)
                                || lifecycleGeneration != generation
                                || bindingGeneration != generation
                                || captureListener != this
                ) {
                    return;
                }
            }
            JSObject event = new JSObject();
            event.put("sequence", chunk.sequence);
            event.put("dataBase64", Base64.encodeToString(chunk.pcm, Base64.NO_WRAP));
            event.put("byteLength", chunk.pcm.length);
            event.put("capturedBytes", chunk.capturedBytes);
            event.put("capturedMilliseconds", chunk.capturedMilliseconds());
            putMediaContract(event);
            notifyListeners("voicePcmChunk", event);
        }

        @Override
        public void onError(String code, String message) {
            synchronized (lifecycleLock) {
                if (
                        (lifecycleState != PluginLifecycle.STARTING
                                && lifecycleState != PluginLifecycle.CAPTURING)
                                || lifecycleGeneration != generation
                                || bindingGeneration != generation
                                || captureListener != this
                ) {
                    return;
                }
            }
            JSObject event = new JSObject();
            event.put("code", code);
            event.put("message", message);
            notifyListeners("voiceCaptureError", event);
        }

        @Override
        public void onStopped(PlaybackAudioCaptureService.CaptureSummary summary) {
            final PendingStart cancelledStart;
            synchronized (lifecycleLock) {
                if (bindingGeneration != generation || captureListener != this) return;
                if (
                        pendingStart != null
                                && pendingStart.generation == generation
                                && !pendingStart.settled
                ) {
                    cancelledStart = pendingStart;
                    cancelledStart.settled = true;
                    pendingStart = null;
                } else {
                    cancelledStart = null;
                }
                if (lifecycleGeneration == generation) lifecycleGeneration += 1L;
                if (lifecycleState != PluginLifecycle.DESTROYED) {
                    lifecycleState = PluginLifecycle.STOPPING;
                }
            }
            if (cancelledStart != null) {
                cancelledStart.call.reject(
                        "Playback capture stopped before initialization completed.",
                        "PLAYBACK_CAPTURE_START_CANCELLED"
                );
            }
            JSObject event = new JSObject();
            event.put("reason", summary.reason);
            notifyListeners("voiceCaptureStopped", event);
            releaseServiceBinding(false, generation);
            synchronized (lifecycleLock) {
                if (lifecycleState == PluginLifecycle.STOPPING) {
                    lifecycleState = PluginLifecycle.IDLE;
                }
            }
        }
    }

    private final class GenerationServiceConnection implements ServiceConnection {
        private final long generation;

        GenerationServiceConnection(long generation) {
            this.generation = generation;
        }

        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            mainHandler.removeCallbacks(serviceBindTimeout);
            if (!(service instanceof PlaybackAudioCaptureService.LocalBinder)) {
                failPendingStartForGeneration(
                        generation,
                        "PLAYBACK_CAPTURE_SERVICE_BIND_FAILED",
                        "Playback capture service returned an unexpected binder.",
                        null
                );
                return;
            }
            PlaybackAudioCaptureService.LocalBinder binder =
                    (PlaybackAudioCaptureService.LocalBinder) service;
            PlaybackAudioCaptureService.Listener listener = new GenerationListener(generation);
            boolean accepted;
            synchronized (lifecycleLock) {
                accepted = lifecycleState == PluginLifecycle.STARTING
                        && lifecycleGeneration == generation
                        && bindingGeneration == generation
                        && serviceConnection == this
                        && pendingStart != null
                        && pendingStart.generation == generation
                        && !pendingStart.settled;
                if (accepted) {
                    bindingRequested = true;
                    serviceBound = true;
                    captureBinder = binder;
                    captureListener = listener;
                }
            }
            if (!accepted) {
                unbindConnectionQuietly(this);
                return;
            }
            binder.setListener(listener);
            beginPendingCapture(generation, binder, listener, this);
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            mainHandler.removeCallbacks(serviceBindTimeout);
            final PendingStart failedStart;
            final boolean reportUnexpectedDisconnect;
            synchronized (lifecycleLock) {
                if (bindingGeneration != generation || serviceConnection != this) return;
                if (
                        pendingStart != null
                                && pendingStart.generation == generation
                                && !pendingStart.settled
                ) {
                    failedStart = pendingStart;
                    failedStart.settled = true;
                    pendingStart = null;
                } else {
                    failedStart = null;
                }
                reportUnexpectedDisconnect = lifecycleState == PluginLifecycle.CAPTURING;
                if (lifecycleGeneration == generation) lifecycleGeneration += 1L;
                captureBinder = null;
                captureListener = null;
                serviceConnection = null;
                bindingRequested = false;
                serviceBound = false;
                bindingGeneration = -1L;
                if (lifecycleState != PluginLifecycle.DESTROYED) {
                    lifecycleState = PluginLifecycle.IDLE;
                }
            }
            if (failedStart != null) {
                failedStart.call.reject(
                        "Playback capture service disconnected while starting.",
                        "PLAYBACK_CAPTURE_SERVICE_DISCONNECTED"
                );
            }
            if (reportUnexpectedDisconnect) {
                JSObject event = new JSObject();
                event.put("code", "PLAYBACK_CAPTURE_SERVICE_DISCONNECTED");
                event.put("message", "Playback capture service disconnected unexpectedly.");
                notifyListeners("voiceCaptureError", event);
            }
            unbindConnectionQuietly(this);
        }

        @Override
        public void onNullBinding(ComponentName name) {
            failPendingStartForGeneration(
                    generation,
                    "PLAYBACK_CAPTURE_SERVICE_BIND_FAILED",
                    "Playback capture service did not provide a binder.",
                    null
            );
        }

        @Override
        public void onBindingDied(ComponentName name) {
            onServiceDisconnected(name);
        }
    }

    private String permissionValue() {
        return getPermissionState("recordAudio").toString().toLowerCase(Locale.US);
    }

    private static void putMediaContract(JSObject value) {
        value.put("sampleRate", SAMPLE_RATE);
        value.put("channels", 1);
        value.put("encoding", "pcm_s16le");
    }

    private static JSObject summaryResult(PlaybackAudioCaptureService.CaptureSummary summary) {
        JSObject result = new JSObject();
        result.put("source", "playback");
        result.put("capturing", summary.capturing);
        result.put("stopped", summary.stopped);
        result.put("nextSequence", summary.nextSequence);
        result.put("capturedBytes", summary.capturedBytes);
        result.put("capturedMilliseconds", summary.capturedMilliseconds());
        result.put("reason", summary.reason);
        putMediaContract(result);
        return result;
    }

    private static int boundedChunkMilliseconds(int requested) {
        int value = requested <= 0 ? DEFAULT_CHUNK_MILLISECONDS : requested;
        return Math.max(100, Math.min(1_000, value));
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

    private static final class PendingStart {
        final PluginCall call;
        final int resultCode;
        final Intent resultData;
        final int chunkMilliseconds;
        final int maxSeconds;
        final long generation;
        boolean settled;

        PendingStart(
                PluginCall call,
                int resultCode,
                Intent resultData,
                int chunkMilliseconds,
                int maxSeconds,
                long generation
        ) {
            this.call = call;
            this.resultCode = resultCode;
            this.resultData = resultData;
            this.chunkMilliseconds = chunkMilliseconds;
            this.maxSeconds = maxSeconds;
            this.generation = generation;
        }
    }

    private static final class PendingStop {
        final long generation;
        final List<PluginCall> calls = new ArrayList<>();

        PendingStop(PluginCall firstCall, long generation) {
            calls.add(firstCall);
            this.generation = generation;
        }

        List<PluginCall> takeCalls() {
            List<PluginCall> result = new ArrayList<>(calls);
            calls.clear();
            return result;
        }
    }

    private static final class PreparedProjection {
        final int resultCode;
        final Intent resultData;
        final long expiresAtElapsedRealtime;
        final long generation;

        PreparedProjection(
                int resultCode,
                Intent resultData,
                long expiresAtElapsedRealtime,
                long generation
        ) {
            this.resultCode = resultCode;
            this.resultData = resultData;
            this.expiresAtElapsedRealtime = expiresAtElapsedRealtime;
            this.generation = generation;
        }
    }
}
