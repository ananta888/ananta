package com.ananta.mobile.voxtral;

import android.Manifest;
import android.app.ActivityManager;
import android.content.pm.ApplicationInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
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
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.zip.GZIPInputStream;

@CapacitorPlugin(
        name = "VoxtralOffline",
        permissions = {
                @Permission(strings = {Manifest.permission.RECORD_AUDIO}, alias = "microphone")
        }
)
public class VoxtralOfflinePlugin extends Plugin {
    private static final long DEFAULT_MIN_FREE_BYTES = 512L * 1024L * 1024L;
    private static final long DEFAULT_MIN_RUNTIME_FREE_BYTES = 640L * 1024L * 1024L;
    private static final long RUNTIME_MODEL_HEADROOM_BYTES = 192L * 1024L * 1024L;
    private static final long RUNTIME_MODEL_MULTIPLIER_NUM = 5L;
    private static final long RUNTIME_MODEL_MULTIPLIER_DEN = 4L;
    private static final long RUNTIME_SAFETY_RESERVE_BYTES = 384L * 1024L * 1024L;
    private static final long LOW_MEMORY_LIVE_MIN_RUNTIME_FREE_BYTES = 512L * 1024L * 1024L;
    private static final long LOW_MEMORY_LIVE_HEADROOM_BYTES = 96L * 1024L * 1024L;
    private static final long LOW_MEMORY_LIVE_MULTIPLIER_NUM = 11L;
    private static final long LOW_MEMORY_LIVE_MULTIPLIER_DEN = 10L;
    private static final long LOW_MEMORY_LIVE_SAFETY_RESERVE_BYTES = 256L * 1024L * 1024L;
    private static final long LIVE_SESSION_MAX_SECONDS = 120L;
    private static final int MAX_PROCESS_OUTPUT_CHARS = 64 * 1024;
    private static final String MODEL_EXTENSION = ".gguf";
    private static final List<String> ALLOWED_DOWNLOAD_HOST_SUFFIXES = Arrays.asList(
            "huggingface.co",
            "github.com",
            "githubusercontent.com"
    );
    private static final List<String> RUNNER_CANDIDATE_NAMES = Arrays.asList(
            "voxtral4b-main",
            "voxtral-stream-cli",
            "voxtral-cli",
            "llama-voxtral-cli",
            "llama-cli",
            "crispasr",
            "crispasr-cli",
            "crispasr-voxtral"
    );
    private static final String DEFAULT_CRISPASR_SOURCE_URL = "https://github.com/CrispStrobe/CrispASR/archive/refs/tags/v0.5.3.tar.gz";
    private static final String PROOT_RUNTIME_SUBDIR = "proot-runtime";
    private static final String LLM_RUNTIME_SUBDIR = "llm-runtime";
    private static final String PREFS_NAME = "voxtral_offline_prefs";
    private static final String PREF_MODEL_PATH = "last_model_path";
    private static final String PREF_RUNNER_PATH = "last_runner_path";
    private static final String CMAKE_VERSION = "3.30.5";
    private static final String CMAKE_ARCHIVE_NAME = "cmake-" + CMAKE_VERSION + "-linux-aarch64.tar.gz";
    private static final String CMAKE_DOWNLOAD_URL = "https://github.com/Kitware/CMake/releases/download/v" + CMAKE_VERSION + "/" + CMAKE_ARCHIVE_NAME;

    private final Object recordingLock = new Object();
    private final ExecutorService ioExecutor = Executors.newSingleThreadExecutor();

    @Override
    public void load() {
        super.load();
        // Self-heal stale proot wrapper targets after APK updates (native lib path changes each install).
        resolveProotWrapper();
    }
    private final PermissionBroker permissionBroker = new PermissionBroker();

    private AudioRecord audioRecord;
    private Thread recordingThread;
    private volatile boolean isRecording;
    private volatile boolean isLiveRunning;
    private Thread liveThread;
    private final StringBuilder liveTranscriptBuffer = new StringBuilder();
    private volatile boolean liveLowMemoryMode;
    private volatile int liveSessionSampleRate = 16000;
    private File liveBufferedWavFile;
    private int liveBufferedPcmBytes;
    private File liveSessionModelFile;
    private File liveSessionRunnerFile;
    private String currentAudioPath;
    private String lastModelPath;
    private String lastRunnerPath;

    @PluginMethod
    public void getStatus(PluginCall call) {
        restoreSelectionState();
        JSObject result = new JSObject();
        result.put("isNative", true);
        result.put("isRecording", isRecording);
        result.put("isLiveRunning", isLiveRunning);
        result.put("microphonePermission", getPermissionState("microphone").toString().toLowerCase());
        result.put("audioPath", currentAudioPath);
        result.put("modelPath", lastModelPath);
        result.put("runnerPath", lastRunnerPath);
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
        requestPermissionForAlias("microphone", call, "permissionResult");
    }

    @PluginMethod
    public void startRecording(PluginCall call) {
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            call.reject("Microphone permission is required.");
            return;
        }
        synchronized (recordingLock) {
            if (isLiveRunning) {
                call.reject("Live transcription is running. Stop live mode first.");
                return;
            }
            if (isRecording) {
                call.reject("Recording already in progress.");
                return;
            }
        }

        Integer maybeMaxSeconds = call.getInt("maxSeconds");
        Integer maybeSampleRate = call.getInt("sampleRate");
        final int maxSeconds = maybeMaxSeconds == null ? 5 : Math.max(1, maybeMaxSeconds);
        final int sampleRate = maybeSampleRate == null ? 16000 : Math.max(8000, Math.min(48000, maybeSampleRate));

        File audioDir = ensureDir("voxtral/audio");
        if (audioDir == null) {
            call.reject("Could not create app-local audio directory.");
            return;
        }
        File outFile = new File(audioDir, "recording_" + System.currentTimeMillis() + ".wav");

        final int channelConfig = AudioFormat.CHANNEL_IN_MONO;
        final int encoding = AudioFormat.ENCODING_PCM_16BIT;
        int minBuffer = AudioRecord.getMinBufferSize(sampleRate, channelConfig, encoding);
        if (minBuffer <= 0) {
            call.reject("AudioRecord buffer initialization failed.");
            return;
        }
        int bufferSize = minBuffer * 2;

        AudioRecord recorder = new AudioRecord(
                MediaRecorder.AudioSource.MIC,
                sampleRate,
                channelConfig,
                encoding,
                bufferSize
        );
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            call.reject("AudioRecord could not be initialized.");
            return;
        }

        synchronized (recordingLock) {
            audioRecord = recorder;
            isRecording = true;
            currentAudioPath = outFile.getAbsolutePath();
            recordingThread = new Thread(() -> recordWav(outFile, sampleRate, bufferSize, maxSeconds), "voxtral-audio-record");
            recordingThread.start();
        }

        JSObject result = new JSObject();
        result.put("audioPath", currentAudioPath);
        result.put("maxSeconds", maxSeconds);
        result.put("sampleRate", sampleRate);
        call.resolve(result);
    }

    @PluginMethod
    public void stopRecording(PluginCall call) {
        Thread toJoin;
        synchronized (recordingLock) {
            if (!isRecording) {
                if (currentAudioPath != null) {
                    JSObject result = new JSObject();
                    result.put("audioPath", currentAudioPath);
                    call.resolve(result);
                    return;
                }
                call.reject("No active recording.");
                return;
            }
            isRecording = false;
            toJoin = recordingThread;
        }

        if (toJoin != null) {
            try {
                toJoin.join(2500);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            if (toJoin.isAlive()) {
                call.reject("Recording is still finalizing. Please wait a moment and try again.");
                return;
            }
        }

        JSObject result = new JSObject();
        result.put("audioPath", currentAudioPath);
        call.resolve(result);
    }

    @PluginMethod
    public void downloadModel(PluginCall call) {
        if (!guardAction(call, "download_model")) return;
        downloadFile(call, "modelUrl", "fileName", "voxtral/models", false, "modelPath", "model");
    }

    @PluginMethod
    public void downloadRunner(PluginCall call) {
        if (!guardAction(call, "download_runner")) return;
        downloadFile(call, "runnerUrl", "fileName", "voxtral/bin", true, "runnerPath", "runner");
    }

    @PluginMethod
    public void provisionVoxtralRunner(PluginCall call) {
        if (!guardAction(call, "download_runner")) return;
        String sourceUrl = call.getString("sourceUrl", DEFAULT_CRISPASR_SOURCE_URL);
        String expectedSourceSha256 = call.getString("sourceSha256");

        File buildDir = ensureDir("voxtral/build");
        File binDir = ensureDir("voxtral/bin");
        File toolsDir = ensureDir("voxtral/tools");
        if (buildDir == null || binDir == null || toolsDir == null) {
            call.reject("Could not prepare voxtral build/bin/tools directories.");
            return;
        }

        ioExecutor.execute(() -> {
            HttpURLConnection connection = null;
            try {
                File runnerBinary = new File(binDir, "crispasr-voxtral");
                File runnerWrapper = new File(binDir, "voxtral-cli");

                // Fast path: avoid expensive rebuild if a valid runner is already provisioned.
                if (runnerWrapper.isFile() && runnerBinary.isFile()) {
                    try {
                        File stagedRunner = prepareRunnerForExecution(runnerWrapper);
                        if (canSpawnRunner(stagedRunner) || canSpawnRunnerViaProot(stagedRunner)) {
                            lastRunnerPath = runnerWrapper.getAbsolutePath();
                            persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
                            JSObject cached = new JSObject();
                            cached.put("runnerPath", runnerWrapper.getAbsolutePath());
                            cached.put("binaryPath", runnerBinary.getAbsolutePath());
                            cached.put("sourceArchivePath", "");
                            cached.put("sourceBytes", 0);
                            cached.put("sourceSha256", "");
                            cached.put("rawOutput", "already_provisioned");
                            call.resolve(cached);
                            return;
                        }
                    } catch (Exception ignored) {
                        // Continue to full provisioning flow.
                    }
                }

                // Bootstrap cmake locally so provisioning does not depend on apt/dpkg.
                File cmakeHome = new File(toolsDir, "cmake-" + CMAKE_VERSION + "-linux-aarch64");
                File cmakeBin = new File(cmakeHome, "bin/cmake");
                File cmakeRequiredModule = new File(cmakeHome, "share/cmake-3.30/Modules/Compiler/IBMCPP-CXX-DetermineVersionInternal.cmake");
                File cmakeArchive = new File(buildDir, CMAKE_ARCHIVE_NAME);
                if (!cmakeArchive.exists() || cmakeArchive.length() < (2L * 1024L * 1024L)) {
                    downloadHttpToFile(CMAKE_DOWNLOAD_URL, cmakeArchive, "runner");
                }

                URL url = new URL(sourceUrl);
                if (!isAllowedDownloadUrl(url)) {
                    call.reject("Network policy denied source URL.");
                    return;
                }

                File sourceArchive = new File(buildDir, "crispasr-source.tar.gz");
                connection = (HttpURLConnection) url.openConnection();
                connection.setConnectTimeout(15000);
                connection.setReadTimeout(120000);
                connection.setInstanceFollowRedirects(true);

                int status = connection.getResponseCode();
                if (status < 200 || status >= 300) {
                    call.reject("Source download failed with HTTP status: " + status);
                    return;
                }

                long totalBytes = connection.getContentLengthLong();
                try (InputStream input = new BufferedInputStream(connection.getInputStream());
                     FileOutputStream output = new FileOutputStream(sourceArchive, false)) {
                    byte[] buffer = new byte[8192];
                    int read;
                    long downloaded = 0;
                    long lastNotified = 0;
                    while ((read = input.read(buffer)) != -1) {
                        output.write(buffer, 0, read);
                        downloaded += read;
                        if (downloaded - lastNotified >= (256 * 1024)) {
                            JSObject event = new JSObject();
                            event.put("type", "runner");
                            event.put("downloadedBytes", downloaded);
                            event.put("totalBytes", totalBytes > 0 ? totalBytes : -1);
                            event.put("progress", totalBytes > 0 ? (double) downloaded / (double) totalBytes : -1);
                            notifyListeners("voxtralDownloadProgress", event);
                            lastNotified = downloaded;
                        }
                    }
                }

                if (!sourceArchive.exists() || sourceArchive.length() == 0) {
                    call.reject("Downloaded source archive is empty.");
                    return;
                }
                if (expectedSourceSha256 != null && !expectedSourceSha256.isBlank()) {
                    String actualSha = computeSha256(sourceArchive);
                    String expectedSha = expectedSourceSha256.trim().toLowerCase();
                    if (!expectedSha.equals(actualSha)) {
                        sourceArchive.delete();
                        call.reject("Source SHA256 mismatch.");
                        return;
                    }
                }

                String innerCommand = ""
                    + "set -e; "
                    + "export PATH=" + shQuote(cmakeHome.getAbsolutePath() + "/bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                    + "BUILD_DIR=" + shQuote(buildDir.getAbsolutePath()) + "; "
                    + "TOOLS_DIR=" + shQuote(toolsDir.getAbsolutePath()) + "; "
                    + "ARCHIVE=" + shQuote(sourceArchive.getAbsolutePath()) + "; "
                    + "CMAKE_ARCHIVE=" + shQuote(cmakeArchive.getAbsolutePath()) + "; "
                    + "CMAKE_HOME=" + shQuote(cmakeHome.getAbsolutePath()) + "; "
                    + "CMAKE_BIN=\"$CMAKE_HOME/bin/cmake\"; "
                    + "CMAKE_REQUIRED=" + shQuote(cmakeRequiredModule.getAbsolutePath()) + "; "
                    + "SRC_DIR=\"$BUILD_DIR/CrispASR\"; "
                    + "if [ ! -x \"$CMAKE_BIN\" ] || [ ! -f \"$CMAKE_REQUIRED\" ]; then "
                    + "rm -rf \"$CMAKE_HOME\"; "
                    + "mkdir -p \"$TOOLS_DIR\"; "
                    + "tar -xzf \"$CMAKE_ARCHIVE\" -C \"$TOOLS_DIR\"; "
                    + "fi; "
                    + "[ -x \"$CMAKE_BIN\" ] || { echo 'cmake bootstrap missing'; exit 41; }; "
                    + "[ -f \"$CMAKE_REQUIRED\" ] || { echo 'cmake modules incomplete'; exit 45; }; "
                    + "command -v cmake >/dev/null 2>&1 || { echo 'cmake bootstrap missing'; exit 41; }; "
                    + "if ! command -v gcc >/dev/null 2>&1 || ! command -v g++ >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then "
                    + "if command -v apt-get >/dev/null 2>&1; then "
                    + "DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null 2>&1 || true; "
                    + "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential make pkg-config >/dev/null 2>&1 || true; "
                    + "fi; "
                    + "fi; "
                    + "command -v gcc >/dev/null 2>&1 || { echo 'gcc not found in proot rootfs'; exit 42; }; "
                    + "command -v g++ >/dev/null 2>&1 || { echo 'g++ not found in proot rootfs'; exit 43; }; "
                    + "command -v make >/dev/null 2>&1 || { echo 'make not found in proot rootfs'; exit 44; }; "
                    + "rm -rf \"$SRC_DIR\" \"$BUILD_DIR/CrispASR-\"*; "
                    + "mkdir -p \"$BUILD_DIR\"; "
                    + "tar -xzf \"$ARCHIVE\" -C \"$BUILD_DIR\"; "
                    + "SRC_CANDIDATE=$(find \"$BUILD_DIR\" -maxdepth 1 -type d -name 'CrispASR-*' | head -n 1); "
                    + "if [ -z \"$SRC_CANDIDATE\" ]; then echo 'CrispASR source dir not found after extract'; exit 31; fi; "
                    + "mv \"$SRC_CANDIDATE\" \"$SRC_DIR\"; "
                    + "cmake -B \"$SRC_DIR/build\" "
                    + "-DCMAKE_BUILD_TYPE=Release "
                    + "-DCRISPASR_BUILD_TESTS=OFF "
                    + "-DCRISPASR_BUILD_EXAMPLES=ON "
                    + "-DCRISPASR_BUILD_SERVER=OFF "
                    + "-DGGML_OPENMP=OFF "
                    + "-DCMAKE_DISABLE_FIND_PACKAGE_OpenMP=ON "
                    + "\"$SRC_DIR\"; "
                    + "cmake --build \"$SRC_DIR/build\" -j2; "
                    + "RUNNER_SRC=\"\"; "
                    + "for c in "
                    + "\"$SRC_DIR/build/bin/crispasr\" "
                    + "\"$SRC_DIR/build/bin/crispasr-cli\" "
                    + "\"$SRC_DIR/build/examples/cli/crispasr\" "
                    + "\"$SRC_DIR/build/examples/cli/crispasr-cli\"; "
                    + "do if [ -x \"$c\" ]; then RUNNER_SRC=\"$c\"; break; fi; done; "
                    + "[ -n \"$RUNNER_SRC\" ] || { echo 'crispasr runner binary not found after build'; find \"$SRC_DIR/build\" -maxdepth 4 -type f -name 'crispasr*' 2>/dev/null; exit 46; }; "
                    + "cp -f \"$RUNNER_SRC\" " + shQuote(runnerBinary.getAbsolutePath()) + "; "
                    + "chmod 700 " + shQuote(runnerBinary.getAbsolutePath()) + "; "
                    + "cat > " + shQuote(runnerWrapper.getAbsolutePath()) + " <<'EOF'\n"
                    + "#!/bin/sh\n"
                    + "exec " + runnerBinary.getAbsolutePath() + " --backend voxtral4b \"$@\"\n"
                    + "EOF\n"
                    + "chmod 700 " + shQuote(runnerWrapper.getAbsolutePath()) + "; ";

                String output = runShellCommandViaProot(innerCommand);
                cmakeBin.setReadable(true, false);
                cmakeBin.setExecutable(true, false);
                File stagedRunner = prepareRunnerForExecution(runnerWrapper);
                if (!canSpawnRunner(stagedRunner) && !canSpawnRunnerViaProot(stagedRunner)) {
                    call.reject("Runner provisioning finished but executable check failed.\n" + output);
                    return;
                }

                lastRunnerPath = runnerWrapper.getAbsolutePath();
                persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
                appendAudit("allow", "provision_runner", "success " + runnerWrapper.getName());

                JSObject result = new JSObject();
                result.put("runnerPath", runnerWrapper.getAbsolutePath());
                result.put("binaryPath", runnerBinary.getAbsolutePath());
                result.put("sourceArchivePath", sourceArchive.getAbsolutePath());
                result.put("sourceBytes", sourceArchive.length());
                result.put("sourceSha256", computeSha256(sourceArchive));
                result.put("rawOutput", output);
                call.resolve(result);
            } catch (Exception error) {
                appendAudit("deny", "provision_runner", "failed " + error.getMessage());
                call.reject("Runner provisioning failed: " + error.getMessage()
                    + "\nIf build tools are unavailable in proot, install Ubuntu distro + toolchain first or place a Voxtral-compatible ARM64 runner manually in .../files/voxtral/bin.");
            } finally {
                if (connection != null) connection.disconnect();
            }
        });
    }

    @PluginMethod
    public void transcribe(PluginCall call) {
        if (!guardAction(call, "transcribe")) return;
        if (isLiveRunning) {
            call.reject("Live transcription is running. Stop live mode first.");
            return;
        }
        synchronized (recordingLock) {
            if (isRecording || recordingThread != null) {
                call.reject("Recording is still active/finalizing. Stop recording and wait a moment.");
                return;
            }
        }
        String audioPath = call.getString("audioPath");
        String modelPath = call.getString("modelPath");
        String runnerPath = call.getString("runnerPath");
        final boolean lowMemoryMode = call.getBoolean("lowMemoryMode", false);
        if (audioPath == null || audioPath.isBlank()) {
            call.reject("audioPath is required.");
            return;
        }
        if (modelPath == null || modelPath.isBlank()) {
            call.reject("modelPath is required.");
            return;
        }
        if (runnerPath == null || runnerPath.isBlank()) {
            call.reject("runnerPath is required.");
            return;
        }

        File audioFile = new File(audioPath);
        File modelFile = new File(modelPath);
        File runnerFile = new File(runnerPath);
        if (!isPathInsideAppSandbox(audioFile) || !isPathInsideAppSandbox(modelFile) || !isPathInsideAppSandbox(runnerFile)) {
            call.reject("Filesystem sandbox violation: paths must be inside app-local storage.");
            return;
        }
        if (!audioFile.exists()) {
            call.reject("Audio file not found: " + audioPath);
            return;
        }
        if (!isValidWavAudio(audioFile)) {
            call.reject("Audio file is invalid or incomplete. Please record again.");
            return;
        }
        if (!modelFile.exists()) {
            call.reject("Model file not found: " + modelPath);
            return;
        }
        if (!runnerFile.exists()) {
            call.reject("Runner file not found: " + runnerPath);
            return;
        }
        if (!isCompatibleModelFile(modelFile)) {
            call.reject("Model must be a .gguf file.");
            return;
        }
        if (!isRunnerCandidate(runnerFile.getName())) {
            call.reject("Runner binary is not a supported Voxtral/llama candidate.");
            return;
        }
        if (!hasEnoughStorageForModel(modelFile, DEFAULT_MIN_FREE_BYTES)) {
            call.reject("Not enough free storage for model execution safety margin.");
            return;
        }
        RuntimeMemoryCheck runtimeMemory = evaluateRuntimeMemoryWithRetry(modelFile, lowMemoryMode);
        if (!runtimeMemory.hasEnoughMemory) {
            call.reject("Not enough available RAM for transcription. Available: "
                    + formatBytes(runtimeMemory.availableBytes)
                    + ", required: " + formatBytes(runtimeMemory.estimatedRequiredBytes)
                    + ". Close other apps or use a smaller model."
                    + (lowMemoryMode ? "" : " You can also enable low-memory mode."));
            return;
        }
        File executableRunnerFile;
        try {
            executableRunnerFile = prepareRunnerForExecution(runnerFile);
        } catch (Exception error) {
            call.reject("Runner preparation failed: " + error.getMessage());
            return;
        }

        lastModelPath = modelPath;
        lastRunnerPath = runnerPath;
        persistSelection(PREF_MODEL_PATH, lastModelPath);
        persistSelection(PREF_RUNNER_PATH, lastRunnerPath);

        ioExecutor.execute(() -> {
            try {
                String output = runRunnerSync(executableRunnerFile, modelFile, audioFile);
                JSObject result = new JSObject();
                result.put("transcript", extractTranscript(output));
                result.put("rawOutput", output);
                result.put("exitCode", 0);
                call.resolve(result);
            } catch (Exception error) {
                call.reject("Transcription failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void clearLastAudio(PluginCall call) {
        if (isLiveRunning) {
            call.reject("Live transcription is running. Stop live mode first.");
            return;
        }
        if (currentAudioPath == null || currentAudioPath.isBlank()) {
            call.resolve();
            return;
        }
        File audio = new File(currentAudioPath);
        if (audio.exists() && !audio.delete()) {
            call.reject("Could not delete audio file: " + currentAudioPath);
            return;
        }
        currentAudioPath = null;
        call.resolve();
    }

    @SuppressWarnings("unused")
    @PermissionCallback
    public void permissionResult(PluginCall call) {
        JSObject result = new JSObject();
        result.put("state", getPermissionState("microphone").toString().toLowerCase());
        call.resolve(result);
    }

    @PluginMethod
    public void startLiveTranscription(PluginCall call) {
        if (!guardAction(call, "start_live")) return;
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            call.reject("Microphone permission is required.");
            return;
        }
        synchronized (recordingLock) {
            if (isRecording) {
                call.reject("Recording already in progress.");
                return;
            }
            if (isLiveRunning) {
                call.reject("Live transcription already running.");
                return;
            }
        }

        String modelPath = call.getString("modelPath");
        String runnerPath = call.getString("runnerPath");
        Integer maybeChunkSeconds = call.getInt("chunkSeconds");
        Integer maybeSampleRate = call.getInt("sampleRate");
        final boolean lowMemoryMode = call.getBoolean("lowMemoryMode", false);
        final int requestedChunkSeconds = maybeChunkSeconds == null ? 3 : Math.max(1, Math.min(10, maybeChunkSeconds));
        final int requestedSampleRate = maybeSampleRate == null ? 16000 : Math.max(8000, Math.min(48000, maybeSampleRate));
        final int chunkSeconds = lowMemoryMode ? 1 : requestedChunkSeconds;
        final int sampleRate = lowMemoryMode ? 8000 : requestedSampleRate;

        if (modelPath == null || modelPath.isBlank()) {
            call.reject("modelPath is required.");
            return;
        }
        if (runnerPath == null || runnerPath.isBlank()) {
            call.reject("runnerPath is required.");
            return;
        }

        File modelFile = new File(modelPath);
        File runnerFile = new File(runnerPath);
        if (!isPathInsideAppSandbox(modelFile) || !isPathInsideAppSandbox(runnerFile)) {
            call.reject("Filesystem sandbox violation: paths must be inside app-local storage.");
            return;
        }
        if (!modelFile.exists()) {
            call.reject("Model file not found: " + modelPath);
            return;
        }
        if (!runnerFile.exists()) {
            call.reject("Runner file not found: " + runnerPath);
            return;
        }
        if (!isCompatibleModelFile(modelFile)) {
            call.reject("Model must be a .gguf file.");
            return;
        }
        if (!isRunnerCandidate(runnerFile.getName())) {
            call.reject("Runner binary is not a supported Voxtral/llama candidate.");
            return;
        }
        if (!hasEnoughStorageForModel(modelFile, DEFAULT_MIN_FREE_BYTES)) {
            call.reject("Not enough free storage for model execution safety margin.");
            return;
        }
        RuntimeMemoryCheck runtimeMemory = evaluateRuntimeMemoryWithRetry(modelFile, lowMemoryMode);
        if (!runtimeMemory.hasEnoughMemory) {
            call.reject("Not enough available RAM for live transcription. Available: "
                    + formatBytes(runtimeMemory.availableBytes)
                    + ", required: " + formatBytes(runtimeMemory.estimatedRequiredBytes)
                    + ". Close other apps or use a smaller model.");
            return;
        }
        File executableRunnerFile;
        try {
            executableRunnerFile = prepareRunnerForExecution(runnerFile);
        } catch (Exception error) {
            call.reject("Runner preparation failed: " + error.getMessage());
            return;
        }

        File liveDir = ensureDir("voxtral/live");
        if (liveDir == null) {
            call.reject("Could not create app-local live directory.");
            return;
        }

        File bufferedWav = null;
        if (lowMemoryMode) {
            bufferedWav = new File(liveDir, "live_buffer_" + System.currentTimeMillis() + ".wav");
            try (FileOutputStream output = new FileOutputStream(bufferedWav, false)) {
                writeWavHeader(output, sampleRate, 1, 16, 0);
            } catch (Exception error) {
                call.reject("Could not prepare low-memory live buffer: " + error.getMessage());
                return;
            }
        }

        synchronized (recordingLock) {
            isLiveRunning = true;
            liveLowMemoryMode = lowMemoryMode;
            liveSessionSampleRate = sampleRate;
            liveBufferedWavFile = bufferedWav;
            liveBufferedPcmBytes = 0;
            liveSessionModelFile = modelFile;
            liveSessionRunnerFile = executableRunnerFile;
            liveTranscriptBuffer.setLength(0);
        }
        lastModelPath = modelPath;
        lastRunnerPath = runnerPath;
        persistSelection(PREF_MODEL_PATH, lastModelPath);
        persistSelection(PREF_RUNNER_PATH, lastRunnerPath);

        long startedAtMs = System.currentTimeMillis();
        liveThread = new Thread(
                () -> runLiveLoop(liveDir, modelFile, executableRunnerFile, sampleRate, chunkSeconds, startedAtMs, lowMemoryMode),
                "voxtral-live-loop"
        );
        liveThread.start();

        JSObject result = new JSObject();
        result.put("started", true);
        result.put("chunkSeconds", chunkSeconds);
        result.put("sampleRate", sampleRate);
        result.put("lowMemoryMode", lowMemoryMode);
        call.resolve(result);
    }

    @PluginMethod
    public void stopLiveTranscription(PluginCall call) {
        Thread toJoin;
        synchronized (recordingLock) {
            if (!isLiveRunning) {
                JSObject result = new JSObject();
                result.put("transcript", liveTranscriptBuffer.toString().trim());
                call.resolve(result);
                resetLiveSessionState(true);
                return;
            }
            isLiveRunning = false;
            toJoin = liveThread;
        }

        if (toJoin != null) {
            try {
                toJoin.join(3000);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        }

        final boolean finalizeLowMemory;
        final File bufferedFile;
        final int bufferedPcmBytes;
        final int sessionSampleRate;
        final File sessionModelFile;
        final File sessionRunnerFile;
        synchronized (recordingLock) {
            finalizeLowMemory = liveLowMemoryMode
                    && liveBufferedWavFile != null
                    && liveBufferedPcmBytes > 0
                    && liveSessionModelFile != null
                    && liveSessionRunnerFile != null;
            bufferedFile = liveBufferedWavFile;
            bufferedPcmBytes = liveBufferedPcmBytes;
            sessionSampleRate = liveSessionSampleRate;
            sessionModelFile = liveSessionModelFile;
            sessionRunnerFile = liveSessionRunnerFile;
        }

        if (finalizeLowMemory && bufferedFile != null && sessionModelFile != null && sessionRunnerFile != null) {
            ioExecutor.execute(() -> {
                try {
                    updateWavHeader(bufferedFile, sessionSampleRate, 1, 16, bufferedPcmBytes);
                    String transcript = runRunnerSync(sessionRunnerFile, sessionModelFile, bufferedFile);
                    synchronized (recordingLock) {
                        liveTranscriptBuffer.setLength(0);
                        if (transcript != null && !transcript.isBlank()) {
                            liveTranscriptBuffer.append(transcript.trim());
                        }
                    }
                    String finalTranscript = liveTranscriptBuffer.toString().trim();
                    JSObject event = new JSObject();
                    event.put("transcript", finalTranscript);
                    notifyListeners("voxtralLiveFinal", event);

                    JSObject result = new JSObject();
                    result.put("transcript", finalTranscript);
                    call.resolve(result);
                } catch (Exception error) {
                    call.reject("Live transcription finalization failed: " + error.getMessage());
                } finally {
                    resetLiveSessionState(true);
                }
            });
            return;
        }

        String finalTranscript = liveTranscriptBuffer.toString().trim();
        JSObject event = new JSObject();
        event.put("transcript", finalTranscript);
        notifyListeners("voxtralLiveFinal", event);

        JSObject result = new JSObject();
        result.put("transcript", finalTranscript);
        call.resolve(result);
        resetLiveSessionState(true);
    }

    @PluginMethod
    public void listLocalAssets(PluginCall call) {
        File modelDir = ensureDir("voxtral/models");
        File runnerDir = ensureDir("voxtral/bin");

        JSObject result = new JSObject();
        result.put("models", listFiles(modelDir, ".gguf"));
        result.put("runners", listFiles(runnerDir, null));
        call.resolve(result);
    }

    @PluginMethod
    public void getFileSha256(PluginCall call) {
        String rawPath = call.getString("path");
        if (rawPath == null || rawPath.isBlank()) {
            call.reject("path is required.");
            return;
        }
        File file = new File(rawPath);
        if (!isPathInsideAppSandbox(file)) {
            call.reject("Filesystem sandbox violation: path must be inside app-local storage.");
            return;
        }
        if (!file.exists() || !file.isFile()) {
            call.reject("File not found: " + rawPath);
            return;
        }
        ioExecutor.execute(() -> {
            try {
                JSObject result = new JSObject();
                result.put("path", file.getAbsolutePath());
                result.put("bytes", file.length());
                result.put("sha256", computeSha256(file));
                call.resolve(result);
            } catch (Exception error) {
                call.reject("SHA256 failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void deleteAsset(PluginCall call) {
        if (!guardAction(call, "delete_asset")) return;
        String rawPath = call.getString("path");
        if (rawPath == null || rawPath.isBlank()) {
            call.reject("path is required.");
            return;
        }
        File target = new File(rawPath);
        if (!isPathInsideAppSandbox(target)) {
            call.reject("Filesystem sandbox violation: path must be inside app-local storage.");
            return;
        }
        File modelDir = ensureDir("voxtral/models");
        File runnerDir = ensureDir("voxtral/bin");
        File audioDir = ensureDir("voxtral/audio");
        if (!isPathInsideAny(target, modelDir, runnerDir, audioDir)) {
            call.reject("Deletion is only allowed for Voxtral model/runner/audio files.");
            return;
        }
        if (!target.exists()) {
            call.reject("File not found: " + rawPath);
            return;
        }
        if (!target.isFile()) {
            call.reject("Path is not a file: " + rawPath);
            return;
        }
        if (!target.delete()) {
            call.reject("Could not delete file: " + rawPath);
            return;
        }

        String deletedPath = target.getAbsolutePath();
        if (deletedPath.equals(lastModelPath)) {
            lastModelPath = "";
            persistSelection(PREF_MODEL_PATH, lastModelPath);
        }
        if (deletedPath.equals(lastRunnerPath)) {
            lastRunnerPath = "";
            persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
        }
        if (deletedPath.equals(currentAudioPath)) {
            currentAudioPath = null;
        }

        JSObject result = new JSObject();
        result.put("path", deletedPath);
        result.put("deleted", true);
        call.resolve(result);
    }

    @PluginMethod
    public void verifySetup(PluginCall call) {
        String modelPath = call.getString("modelPath");
        String runnerPath = call.getString("runnerPath");
        Double maybeMinFreeBytes = call.getDouble("minFreeBytes");
        long minFreeBytes = maybeMinFreeBytes == null
                ? 512L * 1024L * 1024L
                : Math.max(0L, maybeMinFreeBytes.longValue());

        File filesDir = getContext().getFilesDir();
        StatFs statFs = new StatFs(filesDir.getAbsolutePath());
        long availableBytes = statFs.getAvailableBytes();

        JSObject result = new JSObject();
        result.put("availableBytes", availableBytes);
        result.put("hasEnoughStorage", availableBytes >= minFreeBytes);

        if (modelPath != null && !modelPath.isBlank()) {
            File model = new File(modelPath);
            result.put("modelExists", model.exists());
            result.put("modelBytes", model.exists() ? model.length() : 0);
            result.put("modelCompatible", model.exists() && isCompatibleModelFile(model));
            result.put("estimatedRequiredBytes", model.exists() ? Math.max(DEFAULT_MIN_FREE_BYTES, model.length() + (128L * 1024L * 1024L)) : DEFAULT_MIN_FREE_BYTES);
            RuntimeMemoryCheck runtimeMemory = evaluateRuntimeMemory(model);
            result.put("availableRuntimeMemoryBytes", runtimeMemory.availableBytes);
            result.put("estimatedRuntimeRequiredBytes", runtimeMemory.estimatedRequiredBytes);
            result.put("hasEnoughRuntimeMemory", runtimeMemory.hasEnoughMemory);
        } else {
            result.put("modelExists", false);
            result.put("modelBytes", 0);
            result.put("modelCompatible", false);
            result.put("estimatedRequiredBytes", DEFAULT_MIN_FREE_BYTES);
            result.put("availableRuntimeMemoryBytes", 0);
            result.put("estimatedRuntimeRequiredBytes", DEFAULT_MIN_RUNTIME_FREE_BYTES);
            result.put("hasEnoughRuntimeMemory", false);
        }

        if (runnerPath != null && !runnerPath.isBlank()) {
            File runner = new File(runnerPath);
            boolean executable = false;
            boolean runnerModelCompatible = false;
            String runnerProbeMessage = "";
            if (runner.exists()) {
                try {
                    File preparedRunner = prepareRunnerForExecution(runner);
                    executable = canSpawnRunner(preparedRunner) || canSpawnRunnerViaProot(preparedRunner);
                    if (executable && modelPath != null && !modelPath.isBlank()) {
                        File model = new File(modelPath);
                        if (model.exists() && isCompatibleModelFile(model)) {
                            RunnerProbe probe = probeRunnerModelCompatibility(preparedRunner, model);
                            runnerModelCompatible = probe.compatible;
                            runnerProbeMessage = probe.message;
                        }
                    }
                } catch (Exception ignored) {
                    executable = canSpawnRunnerViaProot(runner);
                    runnerModelCompatible = false;
                }
            }
            result.put("runnerExists", runner.exists());
            result.put("runnerExecutable", executable);
            result.put("runnerCompatible", isRunnerCandidate(runner.getName()));
            result.put("runnerModelCompatible", runnerModelCompatible);
            result.put("runnerProbeMessage", runnerProbeMessage);
        } else {
            result.put("runnerExists", false);
            result.put("runnerExecutable", false);
            result.put("runnerCompatible", false);
            result.put("runnerModelCompatible", false);
            result.put("runnerProbeMessage", "");
        }

        call.resolve(result);
    }

    @Override
    protected void handleOnDestroy() {
        synchronized (recordingLock) {
            isRecording = false;
            isLiveRunning = false;
            if (audioRecord != null) {
                try {
                    audioRecord.stop();
                } catch (Exception ignored) {
                }
                audioRecord.release();
                audioRecord = null;
            }
        }
        if (liveThread != null) {
            try {
                liveThread.join(1500);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            liveThread = null;
        }
        resetLiveSessionState(true);
        ioExecutor.shutdownNow();
        super.handleOnDestroy();
    }

    private void runLiveLoop(File liveDir, File modelFile, File runnerFile, int sampleRate, int chunkSeconds, long startedAtMs, boolean lowMemoryMode) {
        while (isLiveRunning) {
            long elapsedSeconds = (System.currentTimeMillis() - startedAtMs) / 1000L;
            if (elapsedSeconds >= LIVE_SESSION_MAX_SECONDS) {
                JSObject event = new JSObject();
                event.put("message", "Live transcription stopped after safety limit (" + LIVE_SESSION_MAX_SECONDS + "s).");
                notifyListeners("voxtralLiveError", event);
                synchronized (recordingLock) {
                    isLiveRunning = false;
                }
                break;
            }
            File chunkFile = new File(liveDir, "chunk_" + System.currentTimeMillis() + ".wav");
            try {
                recordSingleChunk(chunkFile, sampleRate, chunkSeconds);
                if (!chunkFile.exists() || chunkFile.length() == 0) {
                    continue;
                }
                if (lowMemoryMode) {
                    int appended = appendWavChunkData(chunkFile);
                    chunkFile.delete();
                    if (appended <= 0) {
                        continue;
                    }
                    JSObject event = new JSObject();
                    event.put("chunkPath", chunkFile.getAbsolutePath());
                    event.put("partial", "[chunk captured]");
                    event.put("transcript", liveTranscriptBuffer.toString().trim());
                    notifyListeners("voxtralLivePartial", event);
                    continue;
                }
                String partial = runRunnerSync(runnerFile, modelFile, chunkFile);
                if (partial == null || partial.isBlank()) {
                    continue;
                }

                synchronized (recordingLock) {
                    if (liveTranscriptBuffer.length() > 0) liveTranscriptBuffer.append('\n');
                    liveTranscriptBuffer.append(partial.trim());
                }

                JSObject event = new JSObject();
                event.put("chunkPath", chunkFile.getAbsolutePath());
                event.put("partial", partial.trim());
                event.put("transcript", liveTranscriptBuffer.toString().trim());
                notifyListeners("voxtralLivePartial", event);
            } catch (Exception error) {
                JSObject event = new JSObject();
                event.put("message", error.getMessage());
                notifyListeners("voxtralLiveError", event);
                synchronized (recordingLock) {
                    isLiveRunning = false;
                }
                break;
            }
        }
        synchronized (recordingLock) {
            liveThread = null;
        }
    }

    private int appendWavChunkData(File chunkWavFile) throws IOException {
        File target;
        synchronized (recordingLock) {
            target = liveBufferedWavFile;
        }
        if (target == null || chunkWavFile == null || !chunkWavFile.exists()) {
            return 0;
        }
        int written = 0;
        try (FileInputStream input = new FileInputStream(chunkWavFile);
             FileOutputStream output = new FileOutputStream(target, true)) {
            long toSkip = 44;
            while (toSkip > 0) {
                long skipped = input.skip(toSkip);
                if (skipped <= 0) {
                    if (input.read() == -1) return 0;
                    skipped = 1;
                }
                toSkip -= skipped;
            }
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
                written += read;
            }
            output.flush();
        }
        synchronized (recordingLock) {
            liveBufferedPcmBytes += written;
        }
        return written;
    }

    private void recordSingleChunk(File outFile, int sampleRate, int chunkSeconds) throws IOException {
        int channelConfig = AudioFormat.CHANNEL_IN_MONO;
        int encoding = AudioFormat.ENCODING_PCM_16BIT;
        int minBuffer = AudioRecord.getMinBufferSize(sampleRate, channelConfig, encoding);
        if (minBuffer <= 0) throw new IOException("AudioRecord buffer initialization failed.");
        int bufferSize = minBuffer * 2;
        byte[] data = new byte[bufferSize];
        int bytesWritten = 0;

        AudioRecord recorder = new AudioRecord(
                MediaRecorder.AudioSource.MIC,
                sampleRate,
                channelConfig,
                encoding,
                bufferSize
        );
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            throw new IOException("AudioRecord could not be initialized.");
        }

        long deadline = System.currentTimeMillis() + (chunkSeconds * 1000L);
        try (FileOutputStream output = new FileOutputStream(outFile)) {
            writeWavHeader(output, sampleRate, 1, 16, 0);
            recorder.startRecording();
            while (isLiveRunning && System.currentTimeMillis() < deadline) {
                int read = recorder.read(data, 0, data.length);
                if (read > 0) {
                    output.write(data, 0, read);
                    bytesWritten += read;
                }
            }
            updateWavHeader(outFile, sampleRate, 1, 16, bytesWritten);
        } finally {
            try {
                recorder.stop();
            } catch (Exception ignored) {
            }
            recorder.release();
        }
    }

    private String runRunnerSync(File runnerFile, File modelFile, File audioFile) throws IOException, InterruptedException {
        try {
            return runRunnerSyncDirect(runnerFile, modelFile, audioFile);
        } catch (IOException directError) {
            String msg = String.valueOf(directError.getMessage() == null ? "" : directError.getMessage());
            if (!shouldFallbackToProot(msg)) {
                throw directError;
            }
            try {
                return runRunnerSyncViaProot(runnerFile, modelFile, audioFile);
            } catch (IOException prootError) {
                throw new IOException(
                    "Runner direct failed: " + msg + "\n"
                        + "Runner proot fallback failed: " + String.valueOf(prootError.getMessage() == null ? "" : prootError.getMessage())
                );
            }
        }
    }

    private String runRunnerSyncDirect(File runnerFile, File modelFile, File audioFile) throws IOException, InterruptedException {
        List<String> directCommand = buildRunnerCommand(runnerFile, modelFile, audioFile);
        String linkerPath = resolveSystemLinkerPath();
        if (linkerPath != null && !linkerPath.isBlank()) {
            try {
                return executeRunnerViaShellLinker(linkerPath, directCommand);
            } catch (IOException linkerError) {
                String linkerMsg = String.valueOf(linkerError.getMessage() == null ? "" : linkerError.getMessage());
                // If linker mode fails with runtime/model issues, propagate directly.
                if (containsMissingAudioTensorError(linkerMsg) || containsUnsupportedModelArchitecture(linkerMsg)) {
                    throw linkerError;
                }
                // Fall through and attempt direct exec only as a fallback.
            }
        }
        try {
            return executeRunnerCommand(directCommand);
        } catch (IOException directError) {
            String message = String.valueOf(directError.getMessage() == null ? "" : directError.getMessage());
            if (!shouldFallbackToProot(message)) {
                throw directError;
            }
            throw directError;
        }
    }

    private String executeRunnerViaShellLinker(String linkerPath, List<String> runnerCommand) throws IOException, InterruptedException {
        String runnerBinaryPath = resolveRunnerBinaryPath(runnerCommand);
        if (runnerBinaryPath == null || runnerBinaryPath.isBlank()) {
            throw new IOException("Runner command is empty.");
        }
        File runnerBinary = new File(runnerBinaryPath);
        File runnerDir = runnerBinary.getParentFile();
        String nativeLibDir = resolveNativeLibDir();
        String ldLibraryPath = buildRunnerLibraryPath(runnerDir, nativeLibDir);
        String ggmlBackendPath = runnerDir != null ? runnerDir.getAbsolutePath() : "";
        String shellCommand = ""
                + "LD_LIBRARY_PATH=" + shQuote(ldLibraryPath) + " "
                + "GGML_BACKEND_PATH=" + shQuote(ggmlBackendPath) + " "
                + "exec " + shQuote(linkerPath) + " " + joinShellArgs(runnerCommand);
        Process process = new ProcessBuilder("/system/bin/sh", "-lc", shellCommand)
                .redirectErrorStream(true)
                .start();
        String output = readAllLimited(process.getInputStream(), MAX_PROCESS_OUTPUT_CHARS);
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            if (containsMissingAudioTensorError(output)) {
                throw new IOException("Model file is not compatible with Voxtral speech backend: required audio tensors are missing. Use a realtime Voxtral GGUF.");
            }
            if (containsUnsupportedModelArchitecture(output)) {
                throw new IOException("Runner is incompatible with model architecture 'voxtral4b'. Use a Voxtral-compatible runner (not plain llama.cpp).");
            }
            throw new IOException("Runner failed with exit code " + exitCode + "\n" + output);
        }
        return extractTranscript(output);
    }

    private String runRunnerSyncViaProot(File runnerFile, File modelFile, File audioFile) throws IOException, InterruptedException {
        File prootWrapper = resolveProotWrapper();
        File ubuntuRootfs = resolveUbuntuRootfs();
        if (prootWrapper == null || ubuntuRootfs == null || runnerFile == null || !runnerFile.exists()) {
            throw new IOException("Runner execution blocked by Android (permission denied) and proot fallback is unavailable.");
        }
        String wrappedInnerCommand = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
            + "export HOME=/root; export TERM=xterm-256color; "
            + joinShellArgs(buildRunnerCommand(runnerFile, modelFile, audioFile));
        try {
            String output = runShellCommandViaProot(wrappedInnerCommand);
            return extractTranscript(output);
        } catch (IOException error) {
            String message = String.valueOf(error.getMessage() == null ? "" : error.getMessage());
            if (containsMissingAudioTensorError(message)) {
                throw new IOException("Model file is not compatible with Voxtral speech backend: required audio tensors are missing. Use a realtime Voxtral GGUF.");
            }
            if (containsUnsupportedModelArchitecture(message)) {
                throw new IOException("Runner is incompatible with model architecture 'voxtral4b'. Use a Voxtral-compatible runner (not plain llama.cpp).");
            }
            throw new IOException("Runner (proot fallback) failed.\n" + message);
        }
    }

    private String executeRunnerCommand(List<String> command) throws IOException, InterruptedException {
        ProcessBuilder pb = new ProcessBuilder(command);
        pb.redirectErrorStream(true);
        String runnerBinaryPath = resolveRunnerBinaryPath(command);
        if (runnerBinaryPath != null && !runnerBinaryPath.isBlank()) {
            File runnerBinary = new File(runnerBinaryPath);
            File runnerDir = runnerBinary.getParentFile();
            String nativeLibDir = resolveNativeLibDir();
            String existing = pb.environment().get("LD_LIBRARY_PATH");
            StringBuilder ld = new StringBuilder();
            if (runnerDir != null) {
                ld.append(runnerDir.getAbsolutePath());
            }
            if (nativeLibDir != null && !nativeLibDir.isBlank()) {
                if (ld.length() > 0) ld.append(':');
                ld.append(nativeLibDir);
            }
            if (existing != null && !existing.isBlank()) {
                if (ld.length() > 0) ld.append(':');
                ld.append(existing);
            }
            if (ld.length() > 0) {
                pb.environment().put("LD_LIBRARY_PATH", ld.toString());
            }
            if (runnerDir != null) {
                pb.environment().put("GGML_BACKEND_PATH", runnerDir.getAbsolutePath());
            }
        }
        Process process = pb.start();
        String output = readAllLimited(process.getInputStream(), MAX_PROCESS_OUTPUT_CHARS);
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            if (containsMissingAudioTensorError(output)) {
                throw new IOException("Model file is not compatible with Voxtral speech backend: required audio tensors are missing. Use a realtime Voxtral GGUF.");
            }
            if (containsUnsupportedModelArchitecture(output)) {
                throw new IOException("Runner is incompatible with model architecture 'voxtral4b'. Use a Voxtral-compatible runner (not plain llama.cpp).");
            }
            throw new IOException("Runner failed with exit code " + exitCode + "\n" + output);
        }
        return extractTranscript(output);
    }

    private List<String> buildLinkerWrappedCommand(String linkerPath, List<String> runnerCommand) {
        List<String> command = new ArrayList<>();
        command.add(linkerPath);
        command.addAll(runnerCommand);
        return command;
    }

    private String resolveSystemLinkerPath() {
        File linker64 = new File("/system/bin/linker64");
        if (linker64.isFile()) return linker64.getAbsolutePath();
        File linker = new File("/system/bin/linker");
        if (linker.isFile()) return linker.getAbsolutePath();
        return null;
    }

    private String resolveRunnerBinaryPath(List<String> command) {
        if (command == null || command.isEmpty()) return null;
        String first = String.valueOf(command.get(0));
        if ("/system/bin/linker64".equals(first) || "/system/bin/linker".equals(first)) {
            if (command.size() >= 2) return String.valueOf(command.get(1));
            return null;
        }
        return first;
    }

    private String buildRunnerLibraryPath(File runnerDir, String nativeLibDir) {
        StringBuilder builder = new StringBuilder();
        if (runnerDir != null) {
            builder.append(runnerDir.getAbsolutePath());
        }
        if (nativeLibDir != null && !nativeLibDir.isBlank()) {
            if (builder.length() > 0) builder.append(':');
            builder.append(nativeLibDir);
        }
        if (builder.length() > 0) builder.append(':');
        builder.append("${LD_LIBRARY_PATH:-}");
        return builder.toString();
    }

    private RunnerProbe probeRunnerModelCompatibility(File runnerFile, File modelFile) {
        if (runnerFile == null || modelFile == null) return new RunnerProbe(false, "");
        try {
            String output = runModelProbe(runnerFile, modelFile);
            if (containsMissingAudioTensorError(output)) {
                return new RunnerProbe(false, "model missing required audio tensors");
            }
            if (containsUnsupportedModelArchitecture(output)) {
                return new RunnerProbe(false, "unknown model architecture: voxtral4b");
            }
            if (output.contains("failed to load model")) {
                return new RunnerProbe(false, "model load failed");
            }
            return new RunnerProbe(true, "ok");
        } catch (Exception error) {
            String msg = String.valueOf(error.getMessage() == null ? "" : error.getMessage());
            if (containsMissingAudioTensorError(msg)) {
                return new RunnerProbe(false, "model missing required audio tensors");
            }
            if (containsUnsupportedModelArchitecture(msg)) {
                return new RunnerProbe(false, "unknown model architecture: voxtral4b");
            }
            return new RunnerProbe(false, msg.trim());
        }
    }

    private String runModelProbe(File runnerFile, File modelFile) throws IOException, InterruptedException {
        List<String> command = buildRunnerProbeCommand(runnerFile, modelFile);
        Process process = new ProcessBuilder(command)
            .redirectErrorStream(true)
            .start();
        String output = readAllLimited(process.getInputStream(), MAX_PROCESS_OUTPUT_CHARS);
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            File ubuntuRootfs = resolveUbuntuRootfs();
            File prootWrapper = resolveProotWrapper();
            if (ubuntuRootfs != null && prootWrapper != null) {
                return runModelProbeViaProot(runnerFile, modelFile);
            }
        }
        return output;
    }

    private String runModelProbeViaProot(File runnerFile, File modelFile) throws IOException, InterruptedException {
        File prootWrapper = resolveProotWrapper();
        File ubuntuRootfs = resolveUbuntuRootfs();
        if (prootWrapper == null || ubuntuRootfs == null || runnerFile == null || !runnerFile.exists()) {
            return "";
        }
        String wrappedInnerCommand = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
            + "export HOME=/root; export TERM=xterm-256color; "
            + joinShellArgs(buildRunnerProbeCommand(runnerFile, modelFile));
        return runShellCommandViaProot(wrappedInnerCommand);
    }

    private boolean containsUnsupportedModelArchitecture(String text) {
        String normalized = String.valueOf(text == null ? "" : text).toLowerCase();
        return normalized.contains("unknown model architecture")
            || normalized.contains("unsupported model architecture")
            || normalized.contains("model architecture not supported");
    }

    private boolean containsMissingAudioTensorError(String text) {
        String normalized = String.valueOf(text == null ? "" : text).toLowerCase();
        return normalized.contains("missing tensor 'audio.")
            || (normalized.contains("required tensors missing") && normalized.contains("voxtral4b"));
    }

    private boolean shouldFallbackToProot(String message) {
        String normalized = String.valueOf(message == null ? "" : message).toLowerCase();
        return normalized.contains("permission denied")
            || normalized.contains("error=13")
            || normalized.contains("error=2")
            || normalized.contains("no such file or directory")
            || normalized.contains("exec format error")
            || normalized.contains("cannot run program");
    }

    private void recordWav(File outFile, int sampleRate, int bufferSize, int maxSeconds) {
        int bytesWritten = 0;
        byte[] data = new byte[bufferSize];
        long deadline = System.currentTimeMillis() + (maxSeconds * 1000L);

        try (FileOutputStream output = new FileOutputStream(outFile)) {
            writeWavHeader(output, sampleRate, 1, 16, 0);
            AudioRecord recorder;
            synchronized (recordingLock) {
                recorder = audioRecord;
            }
            if (recorder == null) return;

            recorder.startRecording();
            while (isRecording && System.currentTimeMillis() < deadline) {
                int read = recorder.read(data, 0, data.length);
                if (read > 0) {
                    output.write(data, 0, read);
                    bytesWritten += read;
                }
            }
            updateWavHeader(outFile, sampleRate, 1, 16, bytesWritten);
        } catch (Exception error) {
            appendAudit("deny", "record_audio", "record_failed " + error.getClass().getSimpleName() + ": " + String.valueOf(error.getMessage()));
        } finally {
            synchronized (recordingLock) {
                if (audioRecord != null) {
                    try {
                        audioRecord.stop();
                    } catch (Exception ignored) {
                    }
                    audioRecord.release();
                    audioRecord = null;
                }
                if (bytesWritten <= 0 || !isValidWavAudio(outFile)) {
                    if (outFile.exists()) outFile.delete();
                    if (currentAudioPath != null && currentAudioPath.equals(outFile.getAbsolutePath())) {
                        currentAudioPath = null;
                    }
                }
                isRecording = false;
                recordingThread = null;
            }
        }
    }

    private void downloadFile(
            PluginCall call,
            String urlField,
            String fileNameField,
            String targetSubDir,
            boolean executable,
            String outputField,
            String downloadType
    ) {
        String rawUrl = call.getString(urlField);
        String fileName = call.getString(fileNameField);
        String expectedSha256 = call.getString("sha256");
        Double maybeMinBytes = call.getDouble("minBytes");
        long expectedMinBytes = maybeMinBytes == null ? 0L : Math.max(0L, maybeMinBytes.longValue());
        if (rawUrl == null || rawUrl.isBlank()) {
            call.reject(urlField + " is required.");
            return;
        }

        File targetDir = ensureDir(targetSubDir);
        if (targetDir == null) {
            call.reject("Could not create directory: " + targetSubDir);
            return;
        }

        String resolvedFileName = (fileName == null || fileName.isBlank())
                ? inferFileNameFromUrl(rawUrl)
                : fileName.trim();
        File outFile = new File(targetDir, resolvedFileName);

        ioExecutor.execute(() -> {
            HttpURLConnection connection = null;
            try {
                // Idempotent behavior: if the requested file already exists, reuse it and skip network.
                if (outFile.exists() && outFile.length() > 0) {
                    // If minimum expected size is known and existing file is too small, force clean re-download.
                    if (expectedMinBytes > 0 && outFile.length() < expectedMinBytes) {
                        outFile.delete();
                    } else {
                        File existingEffective = finalizeDownloadedFile(
                                outFile,
                                targetDir,
                                executable,
                                expectedSha256,
                                expectedMinBytes,
                                outputField
                        );
                        JSObject existingResult = new JSObject();
                        existingResult.put(outputField, existingEffective.getAbsolutePath());
                        existingResult.put("bytes", existingEffective.length());
                        existingResult.put("sha256", computeSha256(existingEffective));
                        call.resolve(existingResult);
                        return;
                    }
                }

                URL url = new URL(rawUrl);
                if (!isAllowedDownloadUrl(url)) {
                    call.reject("Network policy denied URL: only trusted HTTPS hosts are allowed.");
                    return;
                }
                connection = (HttpURLConnection) url.openConnection();
                connection.setConnectTimeout(15000);
                connection.setReadTimeout(120000);
                connection.setInstanceFollowRedirects(true);

                int status = connection.getResponseCode();
                if (status < 200 || status >= 300) {
                    call.reject("Download failed with HTTP status: " + status);
                    return;
                }
                long totalBytes = connection.getContentLengthLong();

                try (InputStream input = new BufferedInputStream(connection.getInputStream());
                     FileOutputStream output = new FileOutputStream(outFile)) {
                    byte[] buffer = new byte[8192];
                    int read;
                    long downloaded = 0;
                    long lastNotified = 0;
                    while ((read = input.read(buffer)) != -1) {
                        output.write(buffer, 0, read);
                        downloaded += read;
                        if (downloaded - lastNotified >= (256 * 1024)) {
                            JSObject event = new JSObject();
                            event.put("type", downloadType);
                            event.put("downloadedBytes", downloaded);
                            event.put("totalBytes", totalBytes > 0 ? totalBytes : -1);
                            event.put("progress", totalBytes > 0 ? (double) downloaded / (double) totalBytes : -1);
                            notifyListeners("voxtralDownloadProgress", event);
                            lastNotified = downloaded;
                        }
                    }
                }

                if (!outFile.exists() || outFile.length() == 0) {
                    call.reject("Downloaded file is empty: " + outFile.getAbsolutePath());
                    return;
                }

                File effectiveFile = finalizeDownloadedFile(outFile, targetDir, executable, expectedSha256, expectedMinBytes, outputField);
                String sha256 = computeSha256(effectiveFile);

                JSObject result = new JSObject();
                result.put(outputField, effectiveFile.getAbsolutePath());
                result.put("bytes", effectiveFile.length());
                result.put("sha256", sha256);
                appendAudit("allow", downloadType, "download_success " + effectiveFile.getName());
                call.resolve(result);
            } catch (Exception error) {
                appendAudit("deny", downloadType, "download_failed " + error.getMessage());
                call.reject("Download failed: " + error.getMessage());
            } finally {
                if (connection != null) connection.disconnect();
            }
        });
    }

    private File finalizeDownloadedFile(
            File downloadedFile,
            File targetDir,
            boolean executable,
            String expectedSha256,
            long expectedMinBytes,
            String outputField
    ) throws Exception {
        File effectiveFile = downloadedFile;
        if (executable && isTarGzArchive(downloadedFile)) {
            File extracted = extractRunnerFromTarGz(downloadedFile, targetDir);
            if ((extracted == null || !extracted.exists()) && targetDir != null) {
                extracted = selectDefaultRunner(targetDir);
            }
            if (extracted == null || !extracted.exists()) {
                throw new IOException("No runner binary found in archive: " + downloadedFile.getName());
            }
            effectiveFile = extracted;
        }

        String sha256 = computeSha256(effectiveFile);
        if (expectedSha256 != null && !expectedSha256.isBlank()) {
            String normalizedExpected = expectedSha256.trim().toLowerCase();
            if (!normalizedExpected.equals(sha256)) {
                effectiveFile.delete();
                throw new IOException("SHA256 mismatch for " + effectiveFile.getName());
            }
        }
        if (expectedMinBytes > 0 && effectiveFile.length() < expectedMinBytes) {
            effectiveFile.delete();
            throw new IOException("File is smaller than expected minimum size for " + effectiveFile.getName());
        }

        if (executable && !effectiveFile.canExecute()) {
            effectiveFile.setExecutable(true);
        }

        if ("modelPath".equals(outputField)) {
            lastModelPath = effectiveFile.getAbsolutePath();
            persistSelection(PREF_MODEL_PATH, lastModelPath);
        } else if ("runnerPath".equals(outputField)) {
            lastRunnerPath = effectiveFile.getAbsolutePath();
            persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
        }

        return effectiveFile;
    }

    private boolean isTarGzArchive(File file) {
        String name = file.getName().toLowerCase();
        return name.endsWith(".tar.gz") || name.endsWith(".tgz");
    }

    private File extractRunnerFromTarGz(File archive, File targetDir) throws IOException {
        try (InputStream fis = new FileInputStream(archive);
             InputStream gis = new GZIPInputStream(fis);
             BufferedInputStream input = new BufferedInputStream(gis)) {
            byte[] header = new byte[512];
            while (readFully(input, header, 0, header.length)) {
                if (isZeroBlock(header)) break;

                String entryName = readTarString(header, 0, 100);
                long size = parseTarOctal(header, 124, 12);
                char type = (char) (header[156] & 0xff);
                String baseName = baseName(entryName);
                boolean isRegularFile = type == 0 || type == '0';
                boolean candidate = isRegularFile && size > 0 && isRunnerCandidate(baseName);

                File extractedFile = null;
                OutputStreamHolder holder = null;
                if (candidate) {
                    extractedFile = new File(targetDir, baseName);
                    holder = new OutputStreamHolder(new FileOutputStream(extractedFile));
                }

                long remaining = size;
                byte[] buffer = new byte[8192];
                while (remaining > 0) {
                    int read = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
                    if (read == -1) throw new IOException("Unexpected EOF while reading tar entry.");
                    if (holder != null) holder.output.write(buffer, 0, read);
                    remaining -= read;
                }
                if (holder != null) {
                    holder.output.close();
                    if (!extractedFile.canExecute()) extractedFile.setExecutable(true);
                    return extractedFile;
                }

                long padding = (512 - (size % 512)) % 512;
                skipFully(input, padding);
            }
        }
        return null;
    }

    private boolean readFully(InputStream input, byte[] buffer, int offset, int len) throws IOException {
        int total = 0;
        while (total < len) {
            int read = input.read(buffer, offset + total, len - total);
            if (read == -1) {
                return total != 0 && total == len;
            }
            total += read;
        }
        return true;
    }

    private boolean isZeroBlock(byte[] block) {
        for (byte b : block) {
            if (b != 0) return false;
        }
        return true;
    }

    private String readTarString(byte[] buffer, int offset, int len) {
        int end = offset;
        while (end < offset + len && buffer[end] != 0) end += 1;
        return new String(buffer, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    private long parseTarOctal(byte[] buffer, int offset, int len) {
        String raw = readTarString(buffer, offset, len);
        if (raw.isEmpty()) return 0;
        try {
            return Long.parseLong(raw.trim(), 8);
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private String baseName(String path) {
        if (path == null || path.isBlank()) return "";
        String normalized = path.replace('\\', '/');
        int idx = normalized.lastIndexOf('/');
        if (idx < 0) return normalized;
        return normalized.substring(idx + 1);
    }

    private boolean isRunnerCandidate(String baseName) {
        if (baseName == null || baseName.isBlank()) return false;
        String name = baseName.toLowerCase();
        if (RUNNER_CANDIDATE_NAMES.contains(name)) return true;
        return name.contains("voxtral") || name.equals("llama-cli") || name.startsWith("crispasr");
    }

    private boolean isCompatibleModelFile(File modelFile) {
        String name = modelFile.getName().toLowerCase();
        return name.endsWith(MODEL_EXTENSION);
    }

    private boolean hasEnoughStorageForModel(File modelFile, long safetyFloorBytes) {
        File filesDir = getContext().getFilesDir();
        StatFs statFs = new StatFs(filesDir.getAbsolutePath());
        long availableBytes = statFs.getAvailableBytes();
        long estimatedNeeded = Math.max(safetyFloorBytes, modelFile.length() + (128L * 1024L * 1024L));
        return availableBytes >= estimatedNeeded;
    }

    private boolean guardAction(PluginCall call, String action) {
        boolean confirmed = call.getBoolean("confirmed", false);
        boolean allowed = permissionBroker.allows(action, confirmed);
        if (!allowed) {
            appendAudit("deny", action, "default_deny_or_missing_confirmation");
            call.reject("Action blocked by default-deny policy. Explicit confirmation required.");
            return false;
        }
        appendAudit("allow", action, "confirmed_by_user");
        return true;
    }

    private boolean isPathInsideAppSandbox(File file) {
        try {
            File filesDir = getContext().getFilesDir().getCanonicalFile();
            File cacheDir = getContext().getCacheDir().getCanonicalFile();
            File codeCacheDir = getContext().getCodeCacheDir().getCanonicalFile();
            File target = file.getCanonicalFile();
            String path = target.getPath();
            return path.startsWith(filesDir.getPath())
                    || path.startsWith(cacheDir.getPath())
                    || path.startsWith(codeCacheDir.getPath());
        } catch (Exception error) {
            return false;
        }
    }

    private boolean isPathInsideAny(File target, File... allowedDirs) {
        if (target == null || allowedDirs == null) return false;
        try {
            String targetPath = target.getCanonicalPath();
            for (File dir : allowedDirs) {
                if (dir == null) continue;
                String basePath = dir.getCanonicalPath();
                if (targetPath.equals(basePath) || targetPath.startsWith(basePath + File.separator)) {
                    return true;
                }
            }
            return false;
        } catch (Exception error) {
            return false;
        }
    }

    private boolean isAllowedDownloadUrl(URL url) {
        String protocol = String.valueOf(url.getProtocol()).toLowerCase();
        if (!"https".equals(protocol)) return false;
        String host = String.valueOf(url.getHost()).toLowerCase();
        for (String suffix : ALLOWED_DOWNLOAD_HOST_SUFFIXES) {
            if (host.equals(suffix) || host.endsWith("." + suffix)) return true;
        }
        return false;
    }

    private void appendAudit(String decision, String action, String reason) {
        try {
            File auditDir = ensureDir("voxtral");
            if (auditDir == null) return;
            File auditFile = new File(auditDir, "audit.log");
            String line = System.currentTimeMillis() + " decision=" + decision + " action=" + action + " reason=" + reason + "\n";
            try (FileOutputStream out = new FileOutputStream(auditFile, true)) {
                out.write(line.getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {
        }
    }

    private void downloadHttpToFile(String rawUrl, File outFile, String downloadType) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(rawUrl);
            if (!isAllowedDownloadUrl(url)) {
                throw new IOException("Network policy denied URL: " + rawUrl);
            }
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(120000);
            connection.setInstanceFollowRedirects(true);
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("Download failed with HTTP status " + status + " for " + rawUrl);
            }
            long totalBytes = connection.getContentLengthLong();
            try (InputStream input = new BufferedInputStream(connection.getInputStream());
                 FileOutputStream output = new FileOutputStream(outFile, false)) {
                byte[] buffer = new byte[8192];
                int read;
                long downloaded = 0;
                long lastNotified = 0;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    downloaded += read;
                    if (downloaded - lastNotified >= (256 * 1024)) {
                        JSObject event = new JSObject();
                        event.put("type", downloadType);
                        event.put("downloadedBytes", downloaded);
                        event.put("totalBytes", totalBytes > 0 ? totalBytes : -1);
                        event.put("progress", totalBytes > 0 ? (double) downloaded / (double) totalBytes : -1);
                        notifyListeners("voxtralDownloadProgress", event);
                        lastNotified = downloaded;
                    }
                }
                output.flush();
            }
            if (!outFile.exists() || outFile.length() == 0) {
                throw new IOException("Downloaded file is empty: " + outFile.getAbsolutePath());
            }
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void skipFully(InputStream input, long bytes) throws IOException {
        long remaining = bytes;
        while (remaining > 0) {
            long skipped = input.skip(remaining);
            if (skipped <= 0) {
                if (input.read() == -1) {
                    throw new IOException("Unexpected EOF while skipping tar padding.");
                }
                skipped = 1;
            }
            remaining -= skipped;
        }
    }

    private static final class OutputStreamHolder {
        final FileOutputStream output;

        OutputStreamHolder(FileOutputStream output) {
            this.output = output;
        }
    }

    private static final class RunnerProbe {
        final boolean compatible;
        final String message;

        RunnerProbe(boolean compatible, String message) {
            this.compatible = compatible;
            this.message = String.valueOf(message == null ? "" : message);
        }
    }

    private static final class RuntimeMemoryCheck {
        final boolean hasEnoughMemory;
        final long availableBytes;
        final long estimatedRequiredBytes;

        RuntimeMemoryCheck(boolean hasEnoughMemory, long availableBytes, long estimatedRequiredBytes) {
            this.hasEnoughMemory = hasEnoughMemory;
            this.availableBytes = availableBytes;
            this.estimatedRequiredBytes = estimatedRequiredBytes;
        }
    }

    private List<String> buildRunnerCommand(File runnerFile, File modelFile, File audioFile) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        String binaryName = runnerFile.getName();
        if ("voxtral4b-main".equals(binaryName)) {
            command.add(modelFile.getAbsolutePath());
            command.add(audioFile.getAbsolutePath());
            return command;
        }
        if (shouldInjectVoxtralBackend(runnerFile)) {
            command.add("--backend");
            command.add("voxtral4b");
        }
        command.add("-m");
        command.add(modelFile.getAbsolutePath());
        command.add("-f");
        command.add(audioFile.getAbsolutePath());
        return command;
    }

    private List<String> buildRunnerProbeCommand(File runnerFile, File modelFile) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        String binaryName = runnerFile.getName();
        if ("voxtral4b-main".equals(binaryName)) {
            command.add(modelFile.getAbsolutePath());
            return command;
        }
        if (shouldInjectVoxtralBackend(runnerFile)) {
            command.add("--backend");
            command.add("voxtral4b");
        }
        command.add("-m");
        command.add(modelFile.getAbsolutePath());
        command.add("-n");
        command.add("1");
        command.add("-p");
        command.add("ok");
        return command;
    }

    private boolean shouldInjectVoxtralBackend(File runnerFile) {
        if (runnerFile == null) return false;
        String name = String.valueOf(runnerFile.getName()).toLowerCase();
        return name.startsWith("crispasr");
    }

    private RuntimeMemoryCheck evaluateRuntimeMemory(File modelFile) {
        return evaluateRuntimeMemory(modelFile, false);
    }

    private RuntimeMemoryCheck evaluateRuntimeMemoryWithRetry(File modelFile, boolean lowMemoryMode) {
        RuntimeMemoryCheck firstCheck = evaluateRuntimeMemory(modelFile, lowMemoryMode);
        if (firstCheck.hasEnoughMemory) return firstCheck;

        // Try to reclaim Java heap before making a hard reject on tight-memory devices.
        Runtime runtime = Runtime.getRuntime();
        runtime.gc();
        runtime.runFinalization();
        System.gc();

        RuntimeMemoryCheck secondCheck = evaluateRuntimeMemory(modelFile, lowMemoryMode);
        if (secondCheck.availableBytes > firstCheck.availableBytes) return secondCheck;
        return firstCheck;
    }

    private RuntimeMemoryCheck evaluateRuntimeMemory(File modelFile, boolean lowMemoryLiveMode) {
        ActivityManager activityManager = (ActivityManager) getContext().getSystemService(android.content.Context.ACTIVITY_SERVICE);
        if (activityManager == null) {
            return new RuntimeMemoryCheck(false, 0, DEFAULT_MIN_RUNTIME_FREE_BYTES);
        }
        ActivityManager.MemoryInfo memoryInfo = new ActivityManager.MemoryInfo();
        activityManager.getMemoryInfo(memoryInfo);
        long availableBytes = Math.max(0L, memoryInfo.availMem);
        long modelBytes = (modelFile != null && modelFile.exists()) ? Math.max(0L, modelFile.length()) : 0L;
        long estimatedRequiredBytes;
        long reserveBytes;
        if (lowMemoryLiveMode) {
            estimatedRequiredBytes = Math.max(
                    LOW_MEMORY_LIVE_MIN_RUNTIME_FREE_BYTES,
                    Math.max(
                            modelBytes + LOW_MEMORY_LIVE_HEADROOM_BYTES,
                            (modelBytes * LOW_MEMORY_LIVE_MULTIPLIER_NUM) / LOW_MEMORY_LIVE_MULTIPLIER_DEN
                    )
            );
            reserveBytes = LOW_MEMORY_LIVE_SAFETY_RESERVE_BYTES;
        } else {
            estimatedRequiredBytes = Math.max(
                    DEFAULT_MIN_RUNTIME_FREE_BYTES,
                    Math.max(
                            modelBytes + RUNTIME_MODEL_HEADROOM_BYTES,
                            (modelBytes * RUNTIME_MODEL_MULTIPLIER_NUM) / RUNTIME_MODEL_MULTIPLIER_DEN
                    )
            );
            reserveBytes = RUNTIME_SAFETY_RESERVE_BYTES;
        }
        long guardedRequiredBytes = estimatedRequiredBytes + reserveBytes;
        boolean hasEnoughMemory = !memoryInfo.lowMemory && availableBytes >= guardedRequiredBytes;
        return new RuntimeMemoryCheck(hasEnoughMemory, availableBytes, guardedRequiredBytes);
    }

    private String formatBytes(long bytes) {
        if (bytes <= 0) return "0 B";
        String[] units = new String[]{"B", "KB", "MB", "GB", "TB"};
        double value = bytes;
        int index = 0;
        while (value >= 1024 && index < units.length - 1) {
            value /= 1024d;
            index += 1;
        }
        return String.format(java.util.Locale.US, index < 2 ? "%.0f %s" : "%.1f %s", value, units[index]);
    }

    private void resetLiveSessionState(boolean deleteBufferedAudio) {
        File buffered;
        synchronized (recordingLock) {
            buffered = liveBufferedWavFile;
            liveLowMemoryMode = false;
            liveSessionSampleRate = 16000;
            liveBufferedWavFile = null;
            liveBufferedPcmBytes = 0;
            liveSessionModelFile = null;
            liveSessionRunnerFile = null;
        }
        if (deleteBufferedAudio && buffered != null && buffered.exists()) {
            buffered.delete();
        }
    }

    private String joinShellArgs(List<String> args) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < args.size(); i++) {
            if (i > 0) builder.append(' ');
            builder.append(shQuote(args.get(i)));
        }
        return builder.toString();
    }

    private String extractTranscript(String output) {
        if (output == null) return "";
        return output.trim();
    }

    private String readAllLimited(InputStream stream, int maxChars) throws IOException {
        StringBuilder builder = new StringBuilder(Math.min(maxChars, 8192));
        long droppedChars = 0;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            char[] buffer = new char[2048];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                int remaining = maxChars - builder.length();
                if (remaining > 0) {
                    int toAppend = Math.min(remaining, read);
                    builder.append(buffer, 0, toAppend);
                    droppedChars += (read - toAppend);
                } else {
                    droppedChars += read;
                }
            }
        }
        if (droppedChars > 0) {
            builder
                .append("\n...[output truncated: ")
                .append(droppedChars)
                .append(" chars omitted]");
        }
        return builder.toString();
    }

    private boolean isValidWavAudio(File audioFile) {
        if (audioFile == null || !audioFile.exists() || audioFile.length() <= 44) return false;
        try (RandomAccessFile input = new RandomAccessFile(audioFile, "r")) {
            byte[] header = new byte[12];
            input.readFully(header);
            String riff = new String(header, 0, 4, StandardCharsets.US_ASCII);
            String wave = new String(header, 8, 4, StandardCharsets.US_ASCII);
            return "RIFF".equals(riff) && "WAVE".equals(wave);
        } catch (IOException error) {
            return false;
        }
    }

    private File ensureDir(String subPath) {
        File dir = new File(getContext().getFilesDir(), subPath);
        if (dir.exists()) return dir;
        if (dir.mkdirs()) return dir;
        return null;
    }

    private JSArray listFiles(File dir, String extensionFilter) {
        JSArray files = new JSArray();
        if (dir == null || !dir.exists() || !dir.isDirectory()) {
            return files;
        }
        File[] list = dir.listFiles();
        if (list == null) return files;
        for (File file : list) {
            if (!file.isFile()) continue;
            if (extensionFilter != null && !file.getName().toLowerCase().endsWith(extensionFilter)) continue;
            JSObject item = new JSObject();
            item.put("name", file.getName());
            item.put("path", file.getAbsolutePath());
            item.put("bytes", file.length());
            item.put("executable", file.canExecute());
            files.put(item);
        }
        return files;
    }

    private String computeSha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream fis = new FileInputStream(file)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = fis.read(buffer)) > 0) {
                digest.update(buffer, 0, read);
            }
        }
        byte[] hash = digest.digest();
        StringBuilder hex = new StringBuilder(hash.length * 2);
        for (byte b : hash) {
            String part = Integer.toHexString(0xff & b);
            if (part.length() == 1) hex.append('0');
            hex.append(part);
        }
        return hex.toString();
    }

    private void restoreSelectionState() {
        if (lastModelPath == null || lastModelPath.isBlank() || !new File(lastModelPath).isFile()) {
            String persistedModel = prefs().getString(PREF_MODEL_PATH, "");
            if (persistedModel != null && !persistedModel.isBlank() && new File(persistedModel).isFile()) {
                lastModelPath = persistedModel;
            } else {
                File model = selectDefaultAsset(ensureDir("voxtral/models"), MODEL_EXTENSION);
                lastModelPath = model != null ? model.getAbsolutePath() : null;
                if (lastModelPath != null) persistSelection(PREF_MODEL_PATH, lastModelPath);
            }
        }
        if (lastRunnerPath == null || lastRunnerPath.isBlank() || !new File(lastRunnerPath).isFile()) {
            String persistedRunner = prefs().getString(PREF_RUNNER_PATH, "");
            if (persistedRunner != null && !persistedRunner.isBlank() && new File(persistedRunner).isFile()) {
                lastRunnerPath = persistedRunner;
            } else {
                File runner = selectDefaultRunner(ensureDir("voxtral/bin"));
                lastRunnerPath = runner != null ? runner.getAbsolutePath() : null;
                if (lastRunnerPath != null) persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
            }
        }
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS_NAME, 0);
    }

    private void persistSelection(String key, String value) {
        prefs().edit().putString(key, String.valueOf(value == null ? "" : value)).apply();
    }

    private File selectDefaultAsset(File dir, String extensionFilter) {
        if (dir == null || !dir.isDirectory()) return null;
        File[] files = dir.listFiles();
        if (files == null) return null;
        File best = null;
        for (File file : files) {
            if (file == null || !file.isFile()) continue;
            if (extensionFilter != null && !file.getName().toLowerCase().endsWith(extensionFilter)) continue;
            if (best == null || file.lastModified() > best.lastModified()) best = file;
        }
        return best;
    }

    private File selectDefaultRunner(File dir) {
        if (dir == null || !dir.isDirectory()) return null;
        File[] files = dir.listFiles();
        if (files == null) return null;
        File bestPreferred = null;
        File bestFallback = null;
        for (File file : files) {
            if (file == null || !file.isFile()) continue;
            if (!isRunnerCandidate(file.getName())) continue;
            String name = file.getName().toLowerCase();
            if (name.contains("voxtral")) {
                if (bestPreferred == null || file.lastModified() > bestPreferred.lastModified()) {
                    bestPreferred = file;
                }
            } else {
                if (bestFallback == null || file.lastModified() > bestFallback.lastModified()) {
                    bestFallback = file;
                }
            }
        }
        return bestPreferred != null ? bestPreferred : bestFallback;
    }

    private File prepareRunnerForExecution(File sourceRunner) throws IOException {
        if (!sourceRunner.exists()) {
            throw new IOException("Runner file not found: " + sourceRunner.getAbsolutePath());
        }
        sourceRunner.setReadable(true, false);
        sourceRunner.setWritable(true, true);
        sourceRunner.setExecutable(true, false);
        if (sourceRunner.canExecute()) {
            return sourceRunner;
        }
        List<File> candidateDirs = buildExecDirCandidates(sourceRunner);
        IOException lastError = null;

        for (File candidateDir : candidateDirs) {
            File execDir = ensureDir(candidateDir);
            if (execDir == null) continue;
            File stagedRunner = new File(execDir, sourceRunner.getName());
            try {
                boolean needsCopy = !stagedRunner.exists()
                        || stagedRunner.length() != sourceRunner.length()
                        || stagedRunner.lastModified() < sourceRunner.lastModified();
                if (needsCopy) {
                    copyFile(sourceRunner, stagedRunner);
                    stagedRunner.setLastModified(sourceRunner.lastModified());
                }

                stagedRunner.setReadable(true, false);
                stagedRunner.setWritable(true, true);
                stagedRunner.setExecutable(true, false);
                if (!stagedRunner.canExecute()) {
                    throw new IOException("Runner is not executable after staging: " + stagedRunner.getAbsolutePath());
                }
                return stagedRunner;
            } catch (IOException error) {
                lastError = error;
            }
        }
        if (lastError != null) {
            throw lastError;
        }
        throw new IOException("Could not stage runner in an executable directory.");
    }

    private List<File> buildExecDirCandidates(File sourceRunner) {
        List<File> dirs = new ArrayList<>();
        if (sourceRunner != null && sourceRunner.getParentFile() != null) {
            dirs.add(sourceRunner.getParentFile());
        }
        dirs.add(new File(getContext().getFilesDir(), "voxtral/exec"));
        dirs.add(new File(getContext().getNoBackupFilesDir(), "voxtral/exec"));
        dirs.add(new File(getContext().getCacheDir(), "voxtral/exec"));
        return dirs;
    }

    private File ensureDir(File dir) {
        if (dir == null) return null;
        if (dir.exists()) return dir;
        if (dir.mkdirs()) return dir;
        return null;
    }

    private void copyFile(File source, File target) throws IOException {
        try (FileInputStream input = new FileInputStream(source);
             FileOutputStream output = new FileOutputStream(target, false)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            output.flush();
        }
    }

    private boolean canSpawnRunner(File runnerFile) {
        Process process = null;
        try {
            process = new ProcessBuilder(runnerFile.getAbsolutePath())
                    .redirectErrorStream(true)
                    .start();
            process.waitFor(1, TimeUnit.SECONDS);
            return true;
        } catch (Exception ignored) {
            return false;
        } finally {
            if (process != null && process.isAlive()) {
                process.destroy();
            }
        }
    }

    private boolean canSpawnRunnerViaProot(File runnerFile) {
        Process process = null;
        try {
            File prootWrapper = resolveProotWrapper();
            File ubuntuRootfs = resolveUbuntuRootfs();
            if (prootWrapper == null || ubuntuRootfs == null || runnerFile == null || !runnerFile.exists()) return false;
            String runtimePath = new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR).getAbsolutePath();
            String rootfsPath = ubuntuRootfs.getAbsolutePath();
            String nativeLibDir = resolveNativeLibDir();
            String prootLoaderSrc = resolveNativeLibPath("libproot-loader.so");
            String prootLoaderLink = new File(getContext().getDataDir(), "ldr/libproot-loader.so").getAbsolutePath();
            String wrappedInnerCommand = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                + "export HOME=/root; export TERM=xterm-256color; "
                + shQuote(runnerFile.getAbsolutePath()) + " --help >/dev/null 2>&1";
            String cmd = ""
                + "ANANTA_PROOT_RUNTIME=" + shQuote(runtimePath) + "; "
                + "ANANTA_ROOTFS=" + shQuote(rootfsPath) + "; "
                + "ANANTA_PROOT_WRAPPER=" + shQuote(prootWrapper.getAbsolutePath()) + "; "
                + "ANANTA_LIB_DIR=" + shQuote(nativeLibDir == null ? "" : nativeLibDir) + "; "
                + "ANANTA_PROOT_LOADER_SRC=" + shQuote(prootLoaderSrc == null ? "" : prootLoaderSrc) + "; "
                + "ANANTA_PROOT_LOADER_LINK=" + shQuote(prootLoaderLink) + "; "
                + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; "
                + "mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
                + "chmod 755 \"$ANANTA_PROOT_WRAPPER\" 2>/dev/null || true; "
                + "if [ -n \"$ANANTA_PROOT_LOADER_SRC\" ]; then "
                + "mkdir -p \"$(dirname \"$ANANTA_PROOT_LOADER_LINK\")\" 2>/dev/null || true; "
                + "ln -sfn \"$ANANTA_PROOT_LOADER_SRC\" \"$ANANTA_PROOT_LOADER_LINK\" 2>/dev/null || true; "
                + "fi; "
                + "PROOT_LOADER=\"$ANANTA_PROOT_LOADER_LINK\" PROOT_FORCE_KOMPAT=1 GLIBC_TUNABLES=glibc.pthread.rseq=0 "
                + "LD_LIBRARY_PATH=\"$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}\" "
                + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color "
                + "/system/bin/sh \"$ANANTA_PROOT_WRAPPER\" "
                + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" "
                + "-w / /bin/sh -c "
                + shQuote(wrappedInnerCommand);
            process = new ProcessBuilder("/system/bin/sh", "-lc", cmd)
                .redirectErrorStream(true)
                .start();
            return process.waitFor(3, TimeUnit.SECONDS);
        } catch (Exception ignored) {
            return false;
        } finally {
            if (process != null && process.isAlive()) process.destroy();
        }
    }

    private String runShellCommandViaProot(String wrappedInnerCommand) throws IOException, InterruptedException {
        File prootWrapper = resolveProotWrapper();
        File ubuntuRootfs = resolveUbuntuRootfs();
        if (prootWrapper == null || ubuntuRootfs == null) {
            throw new IOException("Proot runtime is unavailable.");
        }
        String runtimePath = new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR).getAbsolutePath();
        String rootfsPath = ubuntuRootfs.getAbsolutePath();
        String nativeLibDir = resolveNativeLibDir();
        String prootLoaderSrc = resolveNativeLibPath("libproot-loader.so");
        String prootLoaderLink = new File(getContext().getDataDir(), "ldr/libproot-loader.so").getAbsolutePath();
        String shellCommand = ""
            + "ANANTA_PROOT_RUNTIME=" + shQuote(runtimePath) + "; "
            + "ANANTA_ROOTFS=" + shQuote(rootfsPath) + "; "
            + "ANANTA_PROOT_WRAPPER=" + shQuote(prootWrapper.getAbsolutePath()) + "; "
            + "ANANTA_LIB_DIR=" + shQuote(nativeLibDir == null ? "" : nativeLibDir) + "; "
            + "ANANTA_PROOT_LOADER_SRC=" + shQuote(prootLoaderSrc == null ? "" : prootLoaderSrc) + "; "
            + "ANANTA_PROOT_LOADER_LINK=" + shQuote(prootLoaderLink) + "; "
            + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; "
            + "mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "chmod 700 \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "chmod 755 \"$ANANTA_PROOT_WRAPPER\" 2>/dev/null || true; "
            + "if [ -n \"$ANANTA_PROOT_LOADER_SRC\" ]; then "
            + "mkdir -p \"$(dirname \"$ANANTA_PROOT_LOADER_LINK\")\" 2>/dev/null || true; "
            + "ln -sfn \"$ANANTA_PROOT_LOADER_SRC\" \"$ANANTA_PROOT_LOADER_LINK\" 2>/dev/null || true; "
            + "fi; "
            + "PROOT_LOADER=\"$ANANTA_PROOT_LOADER_LINK\" PROOT_FORCE_KOMPAT=1 GLIBC_TUNABLES=glibc.pthread.rseq=0 "
            + "LD_LIBRARY_PATH=\"$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}\" "
            + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color "
            + "/system/bin/sh \"$ANANTA_PROOT_WRAPPER\" "
            + "-0 -r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" "
            + "-w / /bin/sh -c "
            + shQuote(wrappedInnerCommand);
        Process process = new ProcessBuilder("/system/bin/sh", "-lc", shellCommand)
            .redirectErrorStream(true)
            .start();
        String output = readAllLimited(process.getInputStream(), MAX_PROCESS_OUTPUT_CHARS);
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IOException("Proot command failed with exit code " + exitCode + "\n" + output);
        }
        return output;
    }

    private File resolveProotWrapper() {
        File runtimeProot = new File(new File(new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR), "bin"), "proot");
        String nativeProotPath = resolveNativeLibPath("libprootclassic.so");
        if (nativeProotPath != null && !nativeProotPath.isBlank()) {
            File parent = runtimeProot.getParentFile();
            if (parent != null && !parent.isDirectory()) {
                parent.mkdirs();
            }
            ensureWrapperTargets(runtimeProot, nativeProotPath);
            if (runtimeProot.canRead() && runtimeProot.canExecute()) return runtimeProot;
        }
        if (runtimeProot.isFile()) {
            runtimeProot.setReadable(true, false);
            runtimeProot.setExecutable(true, false);
            if (runtimeProot.canRead() && runtimeProot.canExecute()) return runtimeProot;
        }
        if (nativeProotPath != null && !nativeProotPath.isBlank()) {
            File direct = new File(nativeProotPath);
            if (direct.isFile() && direct.canExecute()) return direct;
        }
        return null;
    }

    private File resolveUbuntuRootfs() {
        File rootfsDir = new File(new File(new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR), "distros/ubuntu"), "rootfs");
        if (!rootfsDir.isDirectory()) return null;
        if (new File(rootfsDir, "bin/sh").isFile() || new File(rootfsDir, "usr/bin/sh").isFile()) return rootfsDir;
        File[] children = rootfsDir.listFiles();
        if (children == null) return null;
        for (File child : children) {
            if (!child.isDirectory()) continue;
            if (new File(child, "bin/sh").isFile() || new File(child, "usr/bin/sh").isFile()) return child;
        }
        return null;
    }

    private File resolveRuntimeLlamaCli() {
        File llamaCli = new File(new File(new File(getContext().getFilesDir(), LLM_RUNTIME_SUBDIR), "llama-cpp"), "llama-cli");
        return llamaCli.isFile() ? llamaCli : null;
    }

    private String shQuote(String value) {
        String text = String.valueOf(value == null ? "" : value);
        return "'" + text.replace("'", "'\"'\"'") + "'";
    }

    private void ensureWrapperTargets(File wrapperFile, String targetBinaryPath) {
        if (wrapperFile == null || targetBinaryPath == null || targetBinaryPath.isBlank()) return;
        try {
            String script = "#!/system/bin/sh\nexec \"" + targetBinaryPath + "\" \"$@\"\n";
            byte[] bytes = script.getBytes(StandardCharsets.UTF_8);
            boolean needsRewrite = true;
            if (wrapperFile.isFile() && wrapperFile.length() == bytes.length) {
                try (FileInputStream input = new FileInputStream(wrapperFile)) {
                    byte[] existing = new byte[bytes.length];
                    int read = input.read(existing);
                    if (read == bytes.length && Arrays.equals(existing, bytes)) {
                        needsRewrite = false;
                    }
                }
            }
            if (needsRewrite) {
                try (FileOutputStream out = new FileOutputStream(wrapperFile, false)) {
                    out.write(bytes);
                    out.flush();
                }
            }
            wrapperFile.setReadable(true, false);
            wrapperFile.setWritable(true, true);
            wrapperFile.setExecutable(true, false);
        } catch (Exception ignored) {
            // Best effort only.
        }
    }

    private String resolveNativeLibPath(String libName) {
        ApplicationInfo appInfo = getContext().getApplicationInfo();
        if (appInfo == null) return null;
        if (appInfo.nativeLibraryDir != null) {
            File lib = new File(appInfo.nativeLibraryDir, libName);
            if (lib.isFile()) return lib.getAbsolutePath();
        }

        // Fallback path derivation when nativeLibraryDir is stale/unavailable.
        String sourceDir = appInfo.sourceDir;
        if (sourceDir != null && !sourceDir.isBlank()) {
            File apkDir = new File(sourceDir).getParentFile();
            if (apkDir != null) {
                String abi = Build.SUPPORTED_ABIS != null && Build.SUPPORTED_ABIS.length > 0
                    ? String.valueOf(Build.SUPPORTED_ABIS[0])
                    : "";
                String libSubdir = abi.startsWith("arm64") ? "arm64"
                    : abi.startsWith("armeabi") ? "arm"
                    : abi.startsWith("x86_64") ? "x86_64"
                    : abi.startsWith("x86") ? "x86" : "arm64";
                File derived = new File(new File(new File(apkDir, "lib"), libSubdir), libName);
                if (derived.isFile()) return derived.getAbsolutePath();
            }
        }
        return null;
    }

    private String resolveNativeLibDir() {
        ApplicationInfo appInfo = getContext().getApplicationInfo();
        if (appInfo == null || appInfo.nativeLibraryDir == null) return null;
        return appInfo.nativeLibraryDir;
    }

    private String inferFileNameFromUrl(String rawUrl) {
        String trimmed = rawUrl.trim();
        int queryIndex = trimmed.indexOf('?');
        String clean = queryIndex >= 0 ? trimmed.substring(0, queryIndex) : trimmed;
        int slashIndex = clean.lastIndexOf('/');
        if (slashIndex >= 0 && slashIndex < clean.length() - 1) {
            return clean.substring(slashIndex + 1);
        }
        return "download_" + System.currentTimeMillis();
    }

    private void writeWavHeader(FileOutputStream out, int sampleRate, int channels, int bitsPerSample, int dataLength) throws IOException {
        byte[] header = new byte[44];
        int byteRate = sampleRate * channels * bitsPerSample / 8;
        int blockAlign = channels * bitsPerSample / 8;
        int totalDataLen = dataLength + 36;

        header[0] = 'R'; header[1] = 'I'; header[2] = 'F'; header[3] = 'F';
        header[4] = (byte) (totalDataLen & 0xff);
        header[5] = (byte) ((totalDataLen >> 8) & 0xff);
        header[6] = (byte) ((totalDataLen >> 16) & 0xff);
        header[7] = (byte) ((totalDataLen >> 24) & 0xff);
        header[8] = 'W'; header[9] = 'A'; header[10] = 'V'; header[11] = 'E';
        header[12] = 'f'; header[13] = 'm'; header[14] = 't'; header[15] = ' ';
        header[16] = 16; header[17] = 0; header[18] = 0; header[19] = 0;
        header[20] = 1; header[21] = 0;
        header[22] = (byte) channels; header[23] = 0;
        header[24] = (byte) (sampleRate & 0xff);
        header[25] = (byte) ((sampleRate >> 8) & 0xff);
        header[26] = (byte) ((sampleRate >> 16) & 0xff);
        header[27] = (byte) ((sampleRate >> 24) & 0xff);
        header[28] = (byte) (byteRate & 0xff);
        header[29] = (byte) ((byteRate >> 8) & 0xff);
        header[30] = (byte) ((byteRate >> 16) & 0xff);
        header[31] = (byte) ((byteRate >> 24) & 0xff);
        header[32] = (byte) blockAlign; header[33] = 0;
        header[34] = (byte) bitsPerSample; header[35] = 0;
        header[36] = 'd'; header[37] = 'a'; header[38] = 't'; header[39] = 'a';
        header[40] = (byte) (dataLength & 0xff);
        header[41] = (byte) ((dataLength >> 8) & 0xff);
        header[42] = (byte) ((dataLength >> 16) & 0xff);
        header[43] = (byte) ((dataLength >> 24) & 0xff);

        out.write(header, 0, 44);
    }

    private void updateWavHeader(File wavFile, int sampleRate, int channels, int bitsPerSample, int dataLength) throws IOException {
        try (RandomAccessFile raf = new RandomAccessFile(wavFile, "rw")) {
            int byteRate = sampleRate * channels * bitsPerSample / 8;
            int totalDataLen = dataLength + 36;
            raf.seek(4);
            raf.write(intToLittleEndian(totalDataLen));
            raf.seek(24);
            raf.write(intToLittleEndian(sampleRate));
            raf.seek(28);
            raf.write(intToLittleEndian(byteRate));
            raf.seek(40);
            raf.write(intToLittleEndian(dataLength));
        }
    }

    private byte[] intToLittleEndian(int value) {
        return new byte[]{
                (byte) (value & 0xff),
                (byte) ((value >> 8) & 0xff),
                (byte) ((value >> 16) & 0xff),
                (byte) ((value >> 24) & 0xff)
        };
    }
}
