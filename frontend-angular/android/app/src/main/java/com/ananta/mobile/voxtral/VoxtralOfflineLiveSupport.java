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

abstract class VoxtralOfflineLiveSupport extends VoxtralOfflineAssetSupport {
    protected void runLiveLoop(File liveDir, File modelFile, File runnerFile, int sampleRate, int chunkSeconds, long startedAtMs, boolean lowMemoryMode) {
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
                String partial = runRunnerSync(runnerFile, modelFile, chunkFile, false);
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

    protected int appendWavChunkData(File chunkWavFile) throws IOException {
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

    protected void recordSingleChunk(File outFile, int sampleRate, int chunkSeconds) throws IOException {
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

    protected String runRunnerSync(File runnerFile, File modelFile, File audioFile) throws IOException, InterruptedException {
        return runRunnerSync(runnerFile, modelFile, audioFile, false);
    }

    protected String runRunnerSync(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) throws IOException, InterruptedException {
        return runRunnerSyncIsolated(runnerFile, modelFile, audioFile, lowMemoryMode);
    }

    protected String runRunnerSyncIsolated(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) throws IOException {
        try {
            return runRunnerServiceJob("transcribe", runnerFile, modelFile, audioFile, lowMemoryMode);
        } catch (IOException error) {
            if (!shouldFallbackToProot(error)) throw error;
            try {
                return runRunnerSyncViaProot(runnerFile, modelFile, audioFile, lowMemoryMode);
            } catch (InterruptedException interruptedException) {
                Thread.currentThread().interrupt();
                throw new IOException("Runner proot fallback interrupted.");
            }
        }
    }

    protected String runRunnerProbeIsolated(File runnerFile, File modelFile) throws IOException {
        try {
            return runRunnerServiceJob("probe", runnerFile, modelFile, null, false);
        } catch (IOException error) {
            if (!shouldFallbackToProot(error)) throw error;
            try {
                return runModelProbeViaProot(runnerFile, modelFile);
            } catch (InterruptedException interruptedException) {
                Thread.currentThread().interrupt();
                throw new IOException("Runner probe proot fallback interrupted.");
            }
        }
    }

    protected boolean shouldFallbackToProot(IOException error) {
        String message = String.valueOf(error == null ? "" : error.getMessage()).toLowerCase(Locale.US);
        return message.contains("permission denied")
                || message.contains("cannot run program")
                || message.contains("tls segment is underaligned")
                || message.contains("exec format")
                || message.contains("bad executable")
                || message.contains("runner service heartbeat lost")
                || message.contains("runner service timeout");
    }

    protected String runRunnerServiceJob(String mode, File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) throws IOException {
        File jobsDir = ensureDir("voxtral/runner-jobs");
        if (jobsDir == null) {
            throw new IOException("Could not create runner job directory.");
        }
        String jobId = String.valueOf(System.currentTimeMillis()) + "-" + UUID.randomUUID();
        File resultFile = new File(jobsDir, jobId + ".result.json");
        File heartbeatFile = new File(jobsDir, jobId + ".heartbeat");
        long startedAt = System.currentTimeMillis();

        Intent intent = new Intent(getContext(), VoxtralRunnerService.class);
        intent.setAction(VoxtralRunnerService.ACTION_RUN);
        intent.putExtra(VoxtralRunnerService.EXTRA_MODE, mode);
        intent.putExtra(VoxtralRunnerService.EXTRA_RUNNER_PATH, runnerFile == null ? "" : runnerFile.getAbsolutePath());
        intent.putExtra(VoxtralRunnerService.EXTRA_MODEL_PATH, modelFile == null ? "" : modelFile.getAbsolutePath());
        intent.putExtra(VoxtralRunnerService.EXTRA_AUDIO_PATH, audioFile == null ? "" : audioFile.getAbsolutePath());
        intent.putExtra(VoxtralRunnerService.EXTRA_LOW_MEMORY_MODE, lowMemoryMode);
        intent.putExtra(VoxtralRunnerService.EXTRA_RESULT_PATH, resultFile.getAbsolutePath());
        intent.putExtra(VoxtralRunnerService.EXTRA_HEARTBEAT_PATH, heartbeatFile.getAbsolutePath());
        intent.putExtra(VoxtralRunnerService.EXTRA_TIMEOUT_MS, RUNNER_SERVICE_TIMEOUT_MS);

        if (getContext().startService(intent) == null) {
            throw new IOException("Runner service start failed.");
        }

        long lastHeartbeatSeenAt = -1L;
        while (System.currentTimeMillis() - startedAt <= RUNNER_SERVICE_TIMEOUT_MS + 5_000L) {
            if (resultFile.isFile() && resultFile.length() > 0) {
                String json = readTextFile(resultFile);
                resultFile.delete();
                heartbeatFile.delete();
                JSONObject payload;
                try {
                    payload = new JSONObject(json);
                } catch (JSONException jsonException) {
                    throw new IOException("Invalid runner service response JSON.");
                }
                String status = String.valueOf(payload.optString("status", "error")).trim().toLowerCase(Locale.US);
                String rawOutput = String.valueOf(payload.optString("rawOutput", ""));
                if ("ok".equals(status)) {
                    if ("probe".equals(mode)) {
                        return rawOutput;
                    }
                    return String.valueOf(payload.optString("transcript", rawOutput == null ? "" : rawOutput)).trim();
                }
                String error = String.valueOf(payload.optString("error", "runner_service_failure")).trim();
                throw new IOException(error.isBlank() ? "runner_service_failure" : error);
            }

            if (heartbeatFile.isFile()) {
                long heartbeatTs = parseLongSafe(readTextFile(heartbeatFile));
                if (heartbeatTs > 0) {
                    lastHeartbeatSeenAt = System.currentTimeMillis();
                }
            }
            if (lastHeartbeatSeenAt > 0 && (System.currentTimeMillis() - lastHeartbeatSeenAt) > RUNNER_SERVICE_HEARTBEAT_STALE_MS) {
                heartbeatFile.delete();
                resultFile.delete();
                throw new IOException("Runner service heartbeat lost.");
            }
            try {
                Thread.sleep(200L);
            } catch (InterruptedException interruptedException) {
                Thread.currentThread().interrupt();
                throw new IOException("Runner service wait interrupted.");
            }
        }
        heartbeatFile.delete();
        resultFile.delete();
        throw new IOException("Runner service timeout.");
    }

    protected String readTextFile(File file) throws IOException {
        if (file == null || !file.isFile()) return "";
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
            char[] buffer = new char[1024];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                builder.append(buffer, 0, read);
            }
        }
        return builder.toString();
    }

    protected String runRunnerSyncDirect(File runnerFile, File modelFile, File audioFile) throws IOException, InterruptedException {
        return runRunnerSyncDirect(runnerFile, modelFile, audioFile, false);
    }

    protected String runRunnerSyncDirect(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) throws IOException, InterruptedException {
        List<String> directCommand = buildRunnerCommand(runnerFile, modelFile, audioFile, lowMemoryMode);
        String linkerPath = resolveSystemLinkerPath();
        String linkerAttemptError = null;
        if (linkerPath != null && !linkerPath.isBlank()) {
            try {
                return executeRunnerViaShellLinker(linkerPath, directCommand);
            } catch (IOException linkerError) {
                String linkerMsg = String.valueOf(linkerError.getMessage() == null ? "" : linkerError.getMessage());
                linkerAttemptError = linkerMsg;
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
            if (linkerAttemptError != null && !linkerAttemptError.isBlank()) {
                throw new IOException("Runner linker path failed: " + linkerAttemptError + "\nRunner direct failed: " + message);
            }
            throw directError;
        }
    }

    protected String executeRunnerViaShellLinker(String linkerPath, List<String> runnerCommand) throws IOException, InterruptedException {
        String runnerBinaryPath = resolveRunnerBinaryPath(runnerCommand);
        if (runnerBinaryPath == null || runnerBinaryPath.isBlank()) {
            throw new IOException("Runner command is empty.");
        }
        File runnerBinary = new File(runnerBinaryPath);
        File runnerDir = runnerBinary.getParentFile();
        File cpuBackend = ensureCpuBackendAlias(runnerDir);
        String nativeLibDir = resolveNativeLibDir();
        String ldLibraryPath = buildRunnerLibraryPath(runnerDir, nativeLibDir);
        StringBuilder shellCommand = new StringBuilder();
        shellCommand
                .append("LD_LIBRARY_PATH=")
                .append(shQuote(ldLibraryPath))
                .append(" ");
        if (cpuBackend != null && cpuBackend.isFile()) {
            shellCommand
                    .append("GGML_BACKEND_PATH=")
                    .append(shQuote(cpuBackend.getAbsolutePath()))
                    .append(" ");
        }
        shellCommand
                .append("exec ")
                .append(shQuote(linkerPath))
                .append(" ")
                .append(joinShellArgs(runnerCommand));
        Process process = new ProcessBuilder("/system/bin/sh", "-lc", shellCommand.toString())
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

    protected String runRunnerSyncViaProot(File runnerFile, File modelFile, File audioFile) throws IOException, InterruptedException {
        return runRunnerSyncViaProot(runnerFile, modelFile, audioFile, false);
    }

    protected String runnerLibraryPathExport(File runnerFile) {
        if (runnerFile == null || runnerFile.getParentFile() == null) return "";
        return "export LD_LIBRARY_PATH=" + shQuote(runnerFile.getParentFile().getAbsolutePath()) + ":${LD_LIBRARY_PATH:-}; ";
    }

    protected String runRunnerSyncViaProot(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) throws IOException, InterruptedException {
        File prootWrapper = resolveProotWrapper();
        File ubuntuRootfs = resolveUbuntuRootfs();
        if (prootWrapper == null || ubuntuRootfs == null || runnerFile == null || !runnerFile.exists()) {
            throw new IOException("Runner execution blocked by Android (permission denied) and proot fallback is unavailable.");
        }
        String wrappedInnerCommand = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
            + "export HOME=/root; export TERM=xterm-256color; "
            + runnerLibraryPathExport(runnerFile)
            + joinShellArgs(buildRunnerCommand(runnerFile, modelFile, audioFile, lowMemoryMode));
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

    protected String executeRunnerCommand(List<String> command) throws IOException, InterruptedException {
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

    protected List<String> buildLinkerWrappedCommand(String linkerPath, List<String> runnerCommand) {
        List<String> command = new ArrayList<>();
        command.add(linkerPath);
        command.addAll(runnerCommand);
        return command;
    }

    protected String resolveSystemLinkerPath() {
        File linker64 = new File("/system/bin/linker64");
        if (linker64.isFile()) return linker64.getAbsolutePath();
        File linker = new File("/system/bin/linker");
        if (linker.isFile()) return linker.getAbsolutePath();
        return null;
    }

    protected String resolveRunnerBinaryPath(List<String> command) {
        if (command == null || command.isEmpty()) return null;
        String first = String.valueOf(command.get(0));
        if ("/system/bin/linker64".equals(first) || "/system/bin/linker".equals(first)) {
            if (command.size() >= 2) return String.valueOf(command.get(1));
            return null;
        }
        return first;
    }

    protected String buildRunnerLibraryPath(File runnerDir, String nativeLibDir) {
        StringBuilder builder = new StringBuilder();
        if (runnerDir != null) {
            builder.append(runnerDir.getAbsolutePath());
        }
        if (nativeLibDir != null && !nativeLibDir.isBlank()) {
            if (builder.length() > 0) builder.append(':');
            builder.append(nativeLibDir);
        }
        return builder.toString();
    }

    protected File ensureCpuBackendAlias(File runnerDir) {
        if (runnerDir == null || !runnerDir.isDirectory()) return null;
        File alias = new File(runnerDir, "libggml-cpu.so");
        if (alias.isFile()) return alias;
        File[] files = runnerDir.listFiles();
        if (files == null) return null;
        File best = null;
        for (File file : files) {
            if (file == null || !file.isFile()) continue;
            String name = String.valueOf(file.getName()).toLowerCase();
            if (!name.startsWith("libggml-cpu-") || !name.endsWith(".so")) continue;
            if (best == null || file.lastModified() > best.lastModified()) {
                best = file;
            }
        }
        if (best == null) return null;
        try {
            copyFile(best, alias);
            alias.setReadable(true, false);
            alias.setWritable(true, true);
            return alias;
        } catch (Exception ignored) {
            return best;
        }
    }

    protected RunnerProbe probeRunnerModelCompatibility(File runnerFile, File modelFile) {
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

    protected String runModelProbe(File runnerFile, File modelFile) throws IOException, InterruptedException {
        File probeAudio = createProbeAudioFile();
        try {
            return runRunnerSyncIsolated(runnerFile, modelFile, probeAudio, true);
        } finally {
            if (probeAudio != null && probeAudio.exists()) {
                probeAudio.delete();
            }
        }
    }

    protected String runModelProbeViaProot(File runnerFile, File modelFile) throws IOException, InterruptedException {
        File prootWrapper = resolveProotWrapper();
        File ubuntuRootfs = resolveUbuntuRootfs();
        if (prootWrapper == null || ubuntuRootfs == null || runnerFile == null || !runnerFile.exists()) {
            return "";
        }
        File probeAudio = createProbeAudioFile();
        try {
            return runRunnerSyncViaProot(runnerFile, modelFile, probeAudio, true);
        } finally {
            if (probeAudio != null && probeAudio.exists()) {
                probeAudio.delete();
            }
        }
    }

    protected File createProbeAudioFile() throws IOException {
        File probeDir = ensureDir("voxtral/probe");
        if (probeDir == null) {
            throw new IOException("Probe audio directory is unavailable.");
        }
        File probeAudio = new File(probeDir, "probe.wav");
        final int sampleRate = 16000;
        final int channels = 1;
        final int bitsPerSample = 16;
        final int pcmBytes = 3200; // ~100ms silence
        try (FileOutputStream output = new FileOutputStream(probeAudio, false)) {
            writeWavHeader(output, sampleRate, channels, bitsPerSample, pcmBytes);
            output.write(new byte[pcmBytes]);
            output.flush();
        }
        return probeAudio;
    }

    protected boolean containsUnsupportedModelArchitecture(String text) {
        String normalized = String.valueOf(text == null ? "" : text).toLowerCase();
        return normalized.contains("unknown model architecture")
            || normalized.contains("unsupported model architecture")
            || normalized.contains("model architecture not supported");
    }

    protected boolean containsMissingAudioTensorError(String text) {
        String normalized = String.valueOf(text == null ? "" : text).toLowerCase();
        return normalized.contains("missing tensor 'audio.")
            || (normalized.contains("required tensors missing") && normalized.contains("voxtral4b"));
    }

    protected boolean shouldFallbackToProot(String message) {
        String normalized = String.valueOf(message == null ? "" : message).toLowerCase();
        return normalized.contains("permission denied")
            || normalized.contains("error=13")
            || normalized.contains("error=2")
            || normalized.contains("no such file or directory")
            || normalized.contains("exec format error")
            || normalized.contains("cannot run program");
    }

}
