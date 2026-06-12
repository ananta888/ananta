package com.ananta.mobile.voxtral;

import android.Manifest;
import android.app.ActivityManager;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.Uri;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.provider.Settings;
import android.os.StatFs;
import android.content.SharedPreferences;

import com.ananta.mobile.security.PermissionBroker;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.zip.GZIPInputStream;

import org.json.JSONException;
import org.json.JSONObject;

abstract class VoxtralOfflineCore extends Plugin {
    protected static final long DEFAULT_MIN_FREE_BYTES = 512L * 1024L * 1024L;
    protected static final long DEFAULT_MIN_RUNTIME_FREE_BYTES = 640L * 1024L * 1024L;
    protected static final long RUNTIME_MODEL_HEADROOM_BYTES = 192L * 1024L * 1024L;
    protected static final long RUNTIME_MODEL_MULTIPLIER_NUM = 5L;
    protected static final long RUNTIME_MODEL_MULTIPLIER_DEN = 4L;
    protected static final long RUNTIME_SAFETY_RESERVE_BYTES = 768L * 1024L * 1024L;
    protected static final long LOW_MEMORY_LIVE_MIN_RUNTIME_FREE_BYTES = 512L * 1024L * 1024L;
    protected static final long LOW_MEMORY_LIVE_HEADROOM_BYTES = 384L * 1024L * 1024L;
    protected static final long LOW_MEMORY_LIVE_MULTIPLIER_NUM = 3L;
    protected static final long LOW_MEMORY_LIVE_MULTIPLIER_DEN = 2L;
    protected static final long LOW_MEMORY_LIVE_SAFETY_RESERVE_BYTES = 512L * 1024L * 1024L;
    protected static final long MAX_IN_PROCESS_VOXTRAL_MODEL_BYTES = 2_100_000_000L;
    protected static final long MAX_IN_PROCESS_SAFE_PRESET_BYTES = 1_500_000_000L;
    protected static final long LIVE_SESSION_MAX_SECONDS = 120L;
    protected static final int MAX_PROCESS_OUTPUT_CHARS = 64 * 1024;
    protected static final long RUNNER_SERVICE_TIMEOUT_MS = 120_000L;
    protected static final long RUNNER_SERVICE_HEARTBEAT_STALE_MS = 12_000L;
    protected static final String MODEL_EXTENSION = ".gguf";
    protected static final List<String> ALLOWED_DOWNLOAD_HOST_SUFFIXES = Arrays.asList(
            "huggingface.co",
            "github.com",
            "githubusercontent.com"
    );
    protected static final List<String> RUNNER_CANDIDATE_NAMES = Arrays.asList(
            "voxtral4b-main",
            "voxtral-realtime",
            "voxtral-realtime-bin",
            "voxtral-stream-cli",
            "voxtral-cli",
            "llama-voxtral-cli",
            "crispasr",
            "crispasr-cli",
            "crispasr-voxtral"
    );
    protected static final String DEFAULT_VOXTRAL_REALTIME_SOURCE_URL = "https://github.com/andrijdavid/voxtral.cpp/archive/7deef66c8ee473d3ceffc57fb0cd17977eeebca9.tar.gz";
    protected static final String DEFAULT_GGML_SOURCE_URL = "https://github.com/ggml-org/ggml/archive/5cecdad692d868e28dbd2f7c468504770108f30c.tar.gz";
    protected static final String BUNDLED_VOXTRAL_RUNNER_ASSET_DIR = "voxtral-runner";
    protected static final String BUNDLED_VOXTRAL_RUNNER_FILE = "voxtral-realtime";
    protected static final String PROOT_RUNTIME_SUBDIR = "proot-runtime";
    protected static final String LLM_RUNTIME_SUBDIR = "llm-runtime";
    protected static final String PREFS_NAME = "voxtral_offline_prefs";
    protected static final String PREF_MODEL_PATH = "last_model_path";
    protected static final String PREF_RUNNER_PATH = "last_runner_path";
    protected static final String PREF_PROBE_OK_PREFIX = "probe_ok::";
    protected static final String CMAKE_VERSION = "3.30.5";
    protected static final String CMAKE_ARCHIVE_NAME = "cmake-" + CMAKE_VERSION + "-linux-aarch64.tar.gz";
    protected static final String CMAKE_DOWNLOAD_URL = "https://github.com/Kitware/CMake/releases/download/v" + CMAKE_VERSION + "/" + CMAKE_ARCHIVE_NAME;

    protected final Object recordingLock = new Object();
    protected final ExecutorService ioExecutor = Executors.newSingleThreadExecutor();

    protected final PermissionBroker permissionBroker = new PermissionBroker();

    protected AudioRecord audioRecord;
    protected Thread recordingThread;
    protected volatile boolean isRecording;
    protected volatile boolean isLiveRunning;
    protected Thread liveThread;
    protected final StringBuilder liveTranscriptBuffer = new StringBuilder();
    protected volatile boolean liveLowMemoryMode;
    protected volatile int liveSessionSampleRate = 16000;
    protected File liveBufferedWavFile;
    protected int liveBufferedPcmBytes;
    protected File liveSessionModelFile;
    protected File liveSessionRunnerFile;
    protected String currentAudioPath;
    protected String lastModelPath;
    protected String lastRunnerPath;

    protected static final class OutputStreamHolder {
        final FileOutputStream output;

        OutputStreamHolder(FileOutputStream output) {
            this.output = output;
        }
    }

    protected static final class RunnerProbe {
        final boolean compatible;
        final String message;

        RunnerProbe(boolean compatible, String message) {
            this.compatible = compatible;
            this.message = String.valueOf(message == null ? "" : message);
        }
    }

    protected static final class RuntimeMemoryCheck {
        final boolean hasEnoughMemory;
        final long availableBytes;
        final long estimatedRequiredBytes;

        RuntimeMemoryCheck(boolean hasEnoughMemory, long availableBytes, long estimatedRequiredBytes) {
            this.hasEnoughMemory = hasEnoughMemory;
            this.availableBytes = availableBytes;
            this.estimatedRequiredBytes = estimatedRequiredBytes;
        }
    }

}
