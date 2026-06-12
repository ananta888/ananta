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

@CapacitorPlugin(
        name = "VoxtralOffline",
        permissions = {
                @Permission(strings = {Manifest.permission.RECORD_AUDIO}, alias = "microphone")
        }
)
public class VoxtralOfflinePlugin extends VoxtralOfflineLiveSupport {
    @Override
    public void load() {
        super.load();
        // Self-heal stale proot wrapper targets after APK updates (native lib path changes each install).
        resolveProotWrapper();
        installBundledVoxtralRunnerIfAvailable();
    }
    @PluginMethod
    public void getStatus(PluginCall call) {
        installBundledVoxtralRunnerIfAvailable();
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
    public void openAppSettings(PluginCall call) {
        Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
        Uri uri = Uri.fromParts("package", getContext().getPackageName(), null);
        intent.setData(uri);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(intent);
        call.resolve();
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
        String sourceUrl = call.getString("sourceUrl", DEFAULT_VOXTRAL_REALTIME_SOURCE_URL);
        String ggmlSourceUrl = call.getString("ggmlSourceUrl", DEFAULT_GGML_SOURCE_URL);
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
                File runnerBinary = new File(binDir, "voxtral-realtime-bin");
                File runnerWrapper = new File(binDir, "voxtral-realtime");
                installBundledVoxtralRunnerIfAvailable();

                // Fast path: avoid expensive rebuild if a valid runner is already provisioned.
                if (runnerWrapper.isFile()) {
                    try {
                        prepareRunnerForExecution(runnerWrapper);
                        lastRunnerPath = runnerWrapper.getAbsolutePath();
                        persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
                        JSObject cached = new JSObject();
                        cached.put("runnerPath", runnerWrapper.getAbsolutePath());
                        cached.put("binaryPath", runnerBinary.isFile() ? runnerBinary.getAbsolutePath() : runnerWrapper.getAbsolutePath());
                        cached.put("sourceArchivePath", "");
                        cached.put("sourceBytes", 0);
                        cached.put("sourceSha256", "");
                        cached.put("rawOutput", "already_provisioned");
                        call.resolve(cached);
                        return;
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

                File sourceArchive = new File(buildDir, "voxtral-realtime-source.tar.gz");
                if (!sourceArchive.exists() || sourceArchive.length() < (128L * 1024L)) {
                    downloadHttpToFile(sourceUrl, sourceArchive, "runner");
                }
                File ggmlArchive = new File(buildDir, "ggml-source.tar.gz");
                if (!ggmlArchive.exists() || ggmlArchive.length() < (128L * 1024L)) {
                    downloadHttpToFile(ggmlSourceUrl, ggmlArchive, "runner");
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
                    + "GGML_ARCHIVE=" + shQuote(ggmlArchive.getAbsolutePath()) + "; "
                    + "CMAKE_ARCHIVE=" + shQuote(cmakeArchive.getAbsolutePath()) + "; "
                    + "CMAKE_HOME=" + shQuote(cmakeHome.getAbsolutePath()) + "; "
                    + "CMAKE_BIN=\"$CMAKE_HOME/bin/cmake\"; "
                    + "CMAKE_REQUIRED=" + shQuote(cmakeRequiredModule.getAbsolutePath()) + "; "
                    + "SRC_DIR=\"$BUILD_DIR/voxtral.cpp\"; "
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
                    + "rm -rf \"$SRC_DIR\" \"$BUILD_DIR/voxtral.cpp-\"*; "
                    + "for d in \"$BUILD_DIR\"/ggml-*; do [ -d \"$d\" ] && rm -rf \"$d\"; done; "
                    + "mkdir -p \"$BUILD_DIR\"; "
                    + "tar -xzf \"$ARCHIVE\" -C \"$BUILD_DIR\"; "
                    + "SRC_CANDIDATE=$(find \"$BUILD_DIR\" -maxdepth 1 -type d -name 'voxtral.cpp-*' | head -n 1); "
                    + "if [ -z \"$SRC_CANDIDATE\" ]; then echo 'voxtral.cpp source dir not found after extract'; exit 31; fi; "
                    + "mv \"$SRC_CANDIDATE\" \"$SRC_DIR\"; "
                    + "tar -xzf \"$GGML_ARCHIVE\" -C \"$BUILD_DIR\"; "
                    + "GGML_CANDIDATE=$(find \"$BUILD_DIR\" -maxdepth 1 -type d -name 'ggml-*' | head -n 1); "
                    + "if [ -z \"$GGML_CANDIDATE\" ]; then echo 'ggml source dir not found after extract'; exit 32; fi; "
                    + "rm -rf \"$SRC_DIR/ggml\"; "
                    + "mv \"$GGML_CANDIDATE\" \"$SRC_DIR/ggml\"; "
                    + "cmake -B \"$SRC_DIR/build\" "
                    + "-DCMAKE_BUILD_TYPE=Release "
                    + "-DVOXTRAL_NATIVE_OPT=OFF "
                    + "-DVOXTRAL_AUTO_DETECT_BLAS=OFF "
                    + "-DVOXTRAL_AUTO_DETECT_CUDA=OFF "
                    + "-DVOXTRAL_AUTO_DETECT_VULKAN=OFF "
                    + "-DGGML_OPENMP=OFF "
                    + "-DCMAKE_DISABLE_FIND_PACKAGE_OpenMP=ON "
                    + "\"$SRC_DIR\"; "
                    + "cmake --build \"$SRC_DIR/build\" -j2 --target voxtral; "
                    + "RUNNER_SRC=\"\"; "
                    + "for c in "
                    + "\"$SRC_DIR/build/voxtral\" "
                    + "\"$SRC_DIR/build/bin/voxtral\"; "
                    + "do if [ -x \"$c\" ]; then RUNNER_SRC=\"$c\"; break; fi; done; "
                    + "[ -n \"$RUNNER_SRC\" ] || { echo 'voxtral realtime runner binary not found after build'; find \"$SRC_DIR/build\" -maxdepth 4 -type f -name 'voxtral*' 2>/dev/null; exit 46; }; "
                    + "cp -f \"$RUNNER_SRC\" " + shQuote(runnerBinary.getAbsolutePath()) + "; "
                    + "chmod 700 " + shQuote(runnerBinary.getAbsolutePath()) + "; "
                    + "cat > " + shQuote(runnerWrapper.getAbsolutePath()) + " <<'EOF'\n"
                    + "#!/bin/sh\n"
                    + "exec " + runnerBinary.getAbsolutePath() + " \"$@\"\n"
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
        if (!isModelSizeSafeForInProcessRunner(modelFile)) {
            call.reject(buildUnsafeModelSizeMessage(modelFile));
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
        RunnerProbe probeGate = requireRunnerProbeGate(executableRunnerFile, modelFile);
        if (!probeGate.compatible) {
            call.reject("Model/runner probe failed before transcription start: " + probeGate.message);
            return;
        }

        lastModelPath = modelPath;
        lastRunnerPath = runnerPath;
        persistSelection(PREF_MODEL_PATH, lastModelPath);
        persistSelection(PREF_RUNNER_PATH, lastRunnerPath);

        ioExecutor.execute(() -> {
            try {
                RuntimeMemoryCheck freshMemory = evaluateRuntimeMemoryWithRetry(modelFile, lowMemoryMode);
                if (!freshMemory.hasEnoughMemory) {
                    call.reject("Not enough available RAM for transcription. Available: "
                            + formatBytes(freshMemory.availableBytes)
                            + ", required: " + formatBytes(freshMemory.estimatedRequiredBytes)
                            + ". Close other apps or use a smaller model."
                            + (lowMemoryMode ? "" : " You can also enable low-memory mode."));
                    return;
                }
                String output = runRunnerSync(executableRunnerFile, modelFile, audioFile, lowMemoryMode);
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
        if (!isModelSizeSafeForInProcessRunner(modelFile)) {
            call.reject(buildUnsafeModelSizeMessage(modelFile));
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
        RunnerProbe probeGate = requireRunnerProbeGate(executableRunnerFile, modelFile);
        if (!probeGate.compatible) {
            call.reject("Model/runner probe failed before live start: " + probeGate.message);
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
                    String transcript = runRunnerSync(sessionRunnerFile, sessionModelFile, bufferedFile, true);
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
        installBundledVoxtralRunnerIfAvailable();
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
            result.put("modelSafePresetDefault", model.exists() && model.length() <= MAX_IN_PROCESS_SAFE_PRESET_BYTES);
            result.put("estimatedRequiredBytes", model.exists() ? Math.max(DEFAULT_MIN_FREE_BYTES, model.length() + (128L * 1024L * 1024L)) : DEFAULT_MIN_FREE_BYTES);
            RuntimeMemoryCheck runtimeMemory = evaluateRuntimeMemory(model);
            result.put("availableRuntimeMemoryBytes", runtimeMemory.availableBytes);
            result.put("estimatedRuntimeRequiredBytes", runtimeMemory.estimatedRequiredBytes);
            result.put("hasEnoughRuntimeMemory", runtimeMemory.hasEnoughMemory);
        } else {
            result.put("modelExists", false);
            result.put("modelBytes", 0);
            result.put("modelCompatible", false);
            result.put("modelSafePresetDefault", false);
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

}
