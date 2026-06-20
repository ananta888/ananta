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

abstract class VoxtralOfflineAssetSupport extends VoxtralOfflineRuntimeSupport {
    protected abstract RunnerProbe probeRunnerModelCompatibility(File runnerFile, File modelFile);

    protected void recordWav(File outFile, int sampleRate, int bufferSize, int maxSeconds) {
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

    protected void downloadFile(
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
        File tempFile = new File(targetDir, resolvedFileName + ".part");

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

                if (tempFile.exists() && !tempFile.delete()) {
                    throw new IOException("Could not remove partial download: " + tempFile.getAbsolutePath());
                }

                try (InputStream input = new BufferedInputStream(connection.getInputStream());
                     FileOutputStream output = new FileOutputStream(tempFile, false)) {
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

                if (!tempFile.exists() || tempFile.length() == 0) {
                    call.reject("Downloaded file is empty: " + tempFile.getAbsolutePath());
                    return;
                }
                if (outFile.exists() && !outFile.delete()) {
                    throw new IOException("Could not replace existing file: " + outFile.getAbsolutePath());
                }
                if (!tempFile.renameTo(outFile)) {
                    copyFile(tempFile, outFile);
                    if (!tempFile.delete()) {
                        throw new IOException("Could not remove temporary download: " + tempFile.getAbsolutePath());
                    }
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
                if (tempFile.exists()) {
                    tempFile.delete();
                }
                appendAudit("deny", downloadType, "download_failed " + error.getMessage());
                call.reject("Download failed: " + error.getMessage());
            } finally {
                if (connection != null) connection.disconnect();
            }
        });
    }

    protected File finalizeDownloadedFile(
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

    protected boolean isTarGzArchive(File file) {
        String name = file.getName().toLowerCase();
        return name.endsWith(".tar.gz") || name.endsWith(".tgz");
    }

    protected File extractRunnerFromTarGz(File archive, File targetDir) throws IOException {
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

    protected boolean readFully(InputStream input, byte[] buffer, int offset, int len) throws IOException {
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

    protected boolean isZeroBlock(byte[] block) {
        for (byte b : block) {
            if (b != 0) return false;
        }
        return true;
    }

    protected String readTarString(byte[] buffer, int offset, int len) {
        int end = offset;
        while (end < offset + len && buffer[end] != 0) end += 1;
        return new String(buffer, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    protected long parseTarOctal(byte[] buffer, int offset, int len) {
        String raw = readTarString(buffer, offset, len);
        if (raw.isEmpty()) return 0;
        try {
            return Long.parseLong(raw.trim(), 8);
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    protected String baseName(String path) {
        if (path == null || path.isBlank()) return "";
        String normalized = path.replace('\\', '/');
        int idx = normalized.lastIndexOf('/');
        if (idx < 0) return normalized;
        return normalized.substring(idx + 1);
    }

    protected boolean isRunnerCandidate(String baseName) {
        if (baseName == null || baseName.isBlank()) return false;
        String name = baseName.toLowerCase();
        if (RUNNER_CANDIDATE_NAMES.contains(name)) return true;
        return name.contains("voxtral") || name.startsWith("crispasr");
    }

    protected boolean isCompatibleModelFile(File modelFile) {
        String name = modelFile.getName().toLowerCase();
        return name.endsWith(MODEL_EXTENSION);
    }

    protected boolean isModelSizeSafeForInProcessRunner(File modelFile) {
        return modelFile != null && modelFile.length() <= MAX_IN_PROCESS_VOXTRAL_MODEL_BYTES;
    }

    protected String buildUnsafeModelSizeMessage(File modelFile) {
        long modelBytes = modelFile == null ? 0L : modelFile.length();
        return "Model is too large for safe in-app Voxtral execution on this device. Model: "
                + formatBytes(modelBytes)
                + ", safe limit: " + formatBytes(MAX_IN_PROCESS_VOXTRAL_MODEL_BYTES)
                + ". Use a smaller compatible realtime model that passed runner probe on this device.";
    }

    protected RunnerProbe requireRunnerProbeGate(File runnerFile, File modelFile) {
        if (runnerFile == null || modelFile == null) {
            return new RunnerProbe(false, "missing_model_or_runner");
        }
        String key = String.valueOf(runnerFile.getAbsolutePath()) + "::" + String.valueOf(modelFile.getAbsolutePath());
        String cacheKey = PREF_PROBE_OK_PREFIX + Integer.toHexString(key.hashCode());
        String cached = readSelection(cacheKey);
        if ("ok".equalsIgnoreCase(String.valueOf(cached).trim())) {
            return new RunnerProbe(true, "ok(cached)");
        }
        RunnerProbe probe = probeRunnerModelCompatibility(runnerFile, modelFile);
        if (probe.compatible) {
            persistSelection(cacheKey, "ok");
        } else {
            persistSelection(cacheKey, "");
        }
        return probe;
    }

    protected boolean hasEnoughStorageForModel(File modelFile, long safetyFloorBytes) {
        File filesDir = getContext().getFilesDir();
        StatFs statFs = new StatFs(filesDir.getAbsolutePath());
        long availableBytes = statFs.getAvailableBytes();
        long estimatedNeeded = Math.max(safetyFloorBytes, modelFile.length() + (128L * 1024L * 1024L));
        return availableBytes >= estimatedNeeded;
    }

    protected long parseLongSafe(String raw) {
        if (raw == null) return 0L;
        try {
            return Long.parseLong(raw.trim());
        } catch (Exception ignored) {
            return 0L;
        }
    }

    protected boolean guardAction(PluginCall call, String action) {
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

    protected boolean isPathInsideAppSandbox(File file) {
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

    protected boolean isPathInsideAny(File target, File... allowedDirs) {
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

    protected boolean isAllowedDownloadUrl(URL url) {
        String protocol = String.valueOf(url.getProtocol()).toLowerCase();
        if (!"https".equals(protocol)) return false;
        String host = String.valueOf(url.getHost()).toLowerCase();
        for (String suffix : ALLOWED_DOWNLOAD_HOST_SUFFIXES) {
            if (host.equals(suffix) || host.endsWith("." + suffix)) return true;
        }
        return false;
    }

    protected void appendAudit(String decision, String action, String reason) {
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

    protected void downloadHttpToFile(String rawUrl, File outFile, String downloadType) throws IOException {
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

    protected void skipFully(InputStream input, long bytes) throws IOException {
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

}
