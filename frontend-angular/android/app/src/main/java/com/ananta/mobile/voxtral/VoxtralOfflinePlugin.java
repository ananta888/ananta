package com.ananta.mobile.voxtral;

import android.Manifest;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.StatFs;

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
    private static final long LIVE_SESSION_MAX_SECONDS = 120L;
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
            "llama-cli"
    );

    private final Object recordingLock = new Object();
    private final ExecutorService ioExecutor = Executors.newSingleThreadExecutor();
    private final PermissionBroker permissionBroker = new PermissionBroker();

    private AudioRecord audioRecord;
    private Thread recordingThread;
    private volatile boolean isRecording;
    private volatile boolean isLiveRunning;
    private Thread liveThread;
    private final StringBuilder liveTranscriptBuffer = new StringBuilder();
    private String currentAudioPath;
    private String lastModelPath;
    private String lastRunnerPath;

    @PluginMethod
    public void getStatus(PluginCall call) {
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
    public void transcribe(PluginCall call) {
        if (!guardAction(call, "transcribe")) return;
        if (isLiveRunning) {
            call.reject("Live transcription is running. Stop live mode first.");
            return;
        }
        String audioPath = call.getString("audioPath");
        String modelPath = call.getString("modelPath");
        String runnerPath = call.getString("runnerPath");
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
        File executableRunnerFile;
        try {
            executableRunnerFile = prepareRunnerForExecution(runnerFile);
        } catch (Exception error) {
            call.reject("Runner preparation failed: " + error.getMessage());
            return;
        }

        lastModelPath = modelPath;
        lastRunnerPath = runnerPath;

        ioExecutor.execute(() -> {
            try {
                List<String> command = buildRunnerCommand(executableRunnerFile, modelFile, audioFile);
                ProcessBuilder pb = new ProcessBuilder(command);
                pb.redirectErrorStream(true);
                Process process = pb.start();
                String output = readAll(process.getInputStream());
                int exitCode = process.waitFor();
                if (exitCode != 0) {
                    call.reject("Runner failed with exit code " + exitCode + "\n" + output);
                    return;
                }
                JSObject result = new JSObject();
                result.put("transcript", extractTranscript(output));
                result.put("rawOutput", output);
                result.put("exitCode", exitCode);
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
        final int chunkSeconds = maybeChunkSeconds == null ? 3 : Math.max(1, Math.min(10, maybeChunkSeconds));
        final int sampleRate = maybeSampleRate == null ? 16000 : Math.max(8000, Math.min(48000, maybeSampleRate));

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

        synchronized (recordingLock) {
            isLiveRunning = true;
            liveTranscriptBuffer.setLength(0);
        }
        lastModelPath = modelPath;
        lastRunnerPath = runnerPath;

        long startedAtMs = System.currentTimeMillis();
        liveThread = new Thread(
                () -> runLiveLoop(liveDir, modelFile, executableRunnerFile, sampleRate, chunkSeconds, startedAtMs),
                "voxtral-live-loop"
        );
        liveThread.start();

        JSObject result = new JSObject();
        result.put("started", true);
        result.put("chunkSeconds", chunkSeconds);
        result.put("sampleRate", sampleRate);
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

        String finalTranscript = liveTranscriptBuffer.toString().trim();
        JSObject event = new JSObject();
        event.put("transcript", finalTranscript);
        notifyListeners("voxtralLiveFinal", event);

        JSObject result = new JSObject();
        result.put("transcript", finalTranscript);
        call.resolve(result);
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
        } else {
            result.put("modelExists", false);
            result.put("modelBytes", 0);
            result.put("modelCompatible", false);
            result.put("estimatedRequiredBytes", DEFAULT_MIN_FREE_BYTES);
        }

        if (runnerPath != null && !runnerPath.isBlank()) {
            File runner = new File(runnerPath);
            boolean executable = false;
            if (runner.exists()) {
                try {
                    File preparedRunner = prepareRunnerForExecution(runner);
                    executable = canSpawnRunner(preparedRunner);
                } catch (Exception ignored) {
                    executable = false;
                }
            }
            result.put("runnerExists", runner.exists());
            result.put("runnerExecutable", executable);
            result.put("runnerCompatible", isRunnerCandidate(runner.getName()));
        } else {
            result.put("runnerExists", false);
            result.put("runnerExecutable", false);
            result.put("runnerCompatible", false);
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
        ioExecutor.shutdownNow();
        super.handleOnDestroy();
    }

    private void runLiveLoop(File liveDir, File modelFile, File runnerFile, int sampleRate, int chunkSeconds, long startedAtMs) {
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
        List<String> command = buildRunnerCommand(runnerFile, modelFile, audioFile);
        ProcessBuilder pb = new ProcessBuilder(command);
        pb.redirectErrorStream(true);
        Process process = pb.start();
        String output = readAll(process.getInputStream());
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IOException("Runner failed with exit code " + exitCode + "\n" + output);
        }
        return extractTranscript(output);
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
        } catch (Exception ignored) {
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

                File effectiveFile = outFile;
                if (executable && isTarGzArchive(outFile)) {
                    File extracted = extractRunnerFromTarGz(outFile, targetDir);
                    if (extracted == null || !extracted.exists()) {
                        call.reject("No runner binary found in archive: " + outFile.getName());
                        return;
                    }
                    effectiveFile = extracted;
                }

                String sha256 = computeSha256(effectiveFile);
                if (expectedSha256 != null && !expectedSha256.isBlank()) {
                    String normalizedExpected = expectedSha256.trim().toLowerCase();
                    if (!normalizedExpected.equals(sha256)) {
                        effectiveFile.delete();
                        call.reject("SHA256 mismatch for " + effectiveFile.getName());
                        return;
                    }
                }

                if (executable && !effectiveFile.canExecute()) {
                    effectiveFile.setExecutable(true);
                }

                if ("modelPath".equals(outputField)) {
                    lastModelPath = effectiveFile.getAbsolutePath();
                } else if ("runnerPath".equals(outputField)) {
                    lastRunnerPath = effectiveFile.getAbsolutePath();
                }

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
        return name.contains("voxtral") || name.equals("llama-cli");
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

    private List<String> buildRunnerCommand(File runnerFile, File modelFile, File audioFile) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        String binaryName = runnerFile.getName();
        if ("voxtral4b-main".equals(binaryName)) {
            command.add(modelFile.getAbsolutePath());
            command.add(audioFile.getAbsolutePath());
            return command;
        }
        command.add("-m");
        command.add(modelFile.getAbsolutePath());
        command.add("-f");
        command.add(audioFile.getAbsolutePath());
        return command;
    }

    private String extractTranscript(String output) {
        if (output == null) return "";
        return output.trim();
    }

    private String readAll(InputStream stream) throws IOException {
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
        }
        return builder.toString();
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

    private File prepareRunnerForExecution(File sourceRunner) throws IOException {
        if (!sourceRunner.exists()) {
            throw new IOException("Runner file not found: " + sourceRunner.getAbsolutePath());
        }

        File execDir = ensureExecDir();
        if (execDir == null) {
            throw new IOException("Could not create executable runner directory.");
        }

        File stagedRunner = new File(execDir, sourceRunner.getName());
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
    }

    private File ensureExecDir() {
        File dir = new File(getContext().getCodeCacheDir(), "voxtral/exec");
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
