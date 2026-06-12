package com.ananta.mobile.python;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.util.Log;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.lang.reflect.Field;
import java.security.MessageDigest;
import java.util.Map;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.zip.GZIPInputStream;

import org.tukaani.xz.XZInputStream;

abstract class PythonRuntimeArchiveSupport extends PythonRuntimeProotSupport {
    protected File extractFirstExecutableFromTarGz(File archive, File tempDir, String preferredName) throws IOException {
        try (InputStream fis = new FileInputStream(archive);
             InputStream gis = new GZIPInputStream(fis);
             BufferedInputStream input = new BufferedInputStream(gis)) {
            byte[] header = new byte[512];
            while (readFully(input, header, 0, header.length)) {
                if (isZeroBlock(header)) break;
                String entryName = tarEntryName(header);
                long size = parseTarOctal(header, 124, 12);
                char type = (char) (header[156] & 0xff);
                boolean regular = type == 0 || type == '0';
                String baseName = baseName(entryName);
                boolean candidate = regular && size > 0 && (baseName.equals(preferredName) || baseName.startsWith(preferredName));

                File extracted = null;
                FileOutputStream output = null;
                if (candidate) {
                    extracted = new File(tempDir, baseName);
                    output = new FileOutputStream(extracted, false);
                }

                long remaining = size;
                byte[] buffer = new byte[8192];
                while (remaining > 0) {
                    int read = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
                    if (read == -1) throw new IOException("Unexpected EOF while reading tar entry.");
                    if (output != null) output.write(buffer, 0, read);
                    remaining -= read;
                }
                if (output != null) {
                    output.flush();
                    output.close();
                    extracted.setExecutable(true, false);
                    return extracted;
                }
                skipFully(input, (512 - (size % 512)) % 512);
            }
        }
        return null;
    }

    protected void extractTarGzToDirectory(File archive, File targetDir) throws IOException {
        Process process = null;
        try {
            process = new ProcessBuilder(
                "/system/bin/tar",
                "xzf",
                archive.getAbsolutePath(),
                "-C",
                targetDir.getAbsolutePath(),
                "--strip-components=1"
            )
                .redirectErrorStream(true)
                .start();
            boolean finished = process.waitFor(240, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                throw new IOException("tar extraction timeout");
            }
            String output = readProcessOutput(process.getInputStream(), 8_000);
            if (process.exitValue() != 0) {
                throw new IOException(output.isBlank() ? "tar extraction failed with exit " + process.exitValue() : output);
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IOException("tar extraction interrupted", interrupted);
        } finally {
            if (process != null) process.destroy();
        }
    }

    protected void extractTarXzToDirectory(File archive, File targetDir) throws IOException {
        try (InputStream fis = new FileInputStream(archive);
             InputStream xis = new XZInputStream(fis)) {
            String systemTarError = extractTarStreamWithSystemTar(xis, targetDir);
            if (systemTarError == null) {
                return;
            }
            try (InputStream fallbackFis = new FileInputStream(archive);
                 InputStream fallbackXis = new XZInputStream(fallbackFis);
                 BufferedInputStream input = new BufferedInputStream(fallbackXis)) {
                extractTarStreamToDirectory(input, targetDir);
            } catch (IOException parseError) {
                throw new IOException(parseError.getMessage() + " | system tar: " + systemTarError, parseError);
            }
        } catch (IOException parseError) {
            throw parseError;
        }
    }

    protected String installBundledDistroIfAvailable(String distro, File rootfsDir) throws IOException {
        if (!"ubuntu".equals(distro) || !assetExists(UBUNTU_ROOTFS_PRESEED_ASSET)) {
            return null;
        }

        notifyProotProgress("distro", "extracting", "Gebuendelte Ubuntu-Distro wird entpackt.", -1, -1, distro);
        clearDirectory(rootfsDir);
        try {
            extractTarXzAssetToDirectory(UBUNTU_ROOTFS_PRESEED_ASSET, rootfsDir);
        } catch (IOException error) {
            clearDirectory(rootfsDir);
            throw error;
        }

        String version = readAssetTextIfExists(UBUNTU_ROOTFS_PRESEED_VERSION_ASSET);
        if (version == null || version.isBlank()) {
            version = "bundled-ubuntu-rootfs";
        }
        return version.trim();
    }

    protected String installBundledWorkspaceIfAvailable(File workspaceRoot) throws IOException {
        if (!assetExists(ANANTA_WORKSPACE_PRESEED_ASSET)) {
            return null;
        }

        notifyProotProgress("workspace", "extracting", "Gebuendelter Workspace wird entpackt.", -1, -1, "ubuntu");
        if (workspaceRoot.exists()) {
            clearDirectory(workspaceRoot);
        } else if (!workspaceRoot.mkdirs()) {
            throw new IOException("Could not create workspace directory: " + workspaceRoot.getAbsolutePath());
        }

        try {
            extractTarXzAssetToDirectory(ANANTA_WORKSPACE_PRESEED_ASSET, workspaceRoot);
        } catch (IOException error) {
            clearDirectory(workspaceRoot);
            throw error;
        }

        String version = readAssetTextIfExists(ANANTA_WORKSPACE_PRESEED_VERSION_ASSET);
        if (version == null || version.isBlank()) {
            version = "bundled-ananta-workspace";
        }
        return version.trim();
    }

    protected void extractTarXzAssetToDirectory(String assetPath, File targetDir) throws IOException {
        String systemTarError;
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream xzInput = new XZInputStream(assetInput)) {
            systemTarError = extractTarStreamWithSystemTar(xzInput, targetDir, 900);
            if (systemTarError == null) {
                return;
            }
        }

        clearDirectory(targetDir);
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream xzInput = new XZInputStream(assetInput);
             BufferedInputStream input = new BufferedInputStream(xzInput)) {
            extractTarStreamToDirectory(input, targetDir);
        } catch (IOException parseError) {
            throw new IOException(parseError.getMessage() + " | system tar: " + systemTarError, parseError);
        }
    }

    protected void extractTarGzAssetToDirectory(String assetPath, File targetDir) throws IOException {
        String systemTarError;
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream gzipInput = new GZIPInputStream(assetInput)) {
            systemTarError = extractTarStreamWithSystemTar(gzipInput, targetDir, 300);
            if (systemTarError == null) {
                return;
            }
        }

        clearDirectory(targetDir);
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream gzipInput = new GZIPInputStream(assetInput);
             BufferedInputStream input = new BufferedInputStream(gzipInput)) {
            extractTarStreamToDirectory(input, targetDir);
        } catch (IOException parseError) {
            throw new IOException(parseError.getMessage() + " | system tar: " + systemTarError, parseError);
        }
    }

    protected boolean assetExists(String assetPath) {
        Context context = getContext();
        if (context == null) return false;
        try (InputStream ignored = context.getAssets().open(assetPath)) {
            return true;
        } catch (IOException ignored) {
            return false;
        }
    }

    protected String readAssetTextIfExists(String assetPath) throws IOException {
        Context context = getContext();
        if (context == null || !assetExists(assetPath)) return null;
        try (InputStream input = context.getAssets().open(assetPath)) {
            return readProcessOutput(input, 4_000).trim();
        }
    }

    protected void writeTextFile(File file, String content) throws IOException {
        ensureParent(file);
        try (FileOutputStream output = new FileOutputStream(file, false)) {
            output.write(String.valueOf(content == null ? "" : content).getBytes(StandardCharsets.UTF_8));
            output.flush();
        }
    }

    protected String extractTarStreamWithSystemTar(InputStream tarStream, File targetDir) {
        return extractTarStreamWithSystemTar(tarStream, targetDir, 240);
    }

    protected String extractTarStreamWithSystemTar(InputStream tarStream, File targetDir, int timeoutSeconds) {
        Process process = null;
        try {
            process = new ProcessBuilder(
                "/system/bin/tar",
                "-xf",
                "-",
                "-C",
                targetDir.getAbsolutePath()
            )
                .redirectErrorStream(true)
                .start();
            try (OutputStream processInput = process.getOutputStream()) {
                copyStream(tarStream, processInput);
            }
            boolean finished = process.waitFor(timeoutSeconds, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                return "timeout";
            }
            String output = readProcessOutput(process.getInputStream(), 8_000);
            if (process.exitValue() == 0) return null;
            return output.isBlank() ? "exit " + process.exitValue() : output;
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage() == null ? "" : error.getMessage()).trim();
            if (message.isEmpty()) message = error.getClass().getSimpleName();
            return message;
        } finally {
            if (process != null) process.destroy();
        }
    }

    protected File resolveInstalledRootfs(File rootfsDir) {
        if (rootfsDir == null || !rootfsDir.isDirectory()) return null;
        if (resolveLoginShellPath(rootfsDir) != null) return rootfsDir;
        File[] children = rootfsDir.listFiles();
        if (children == null) return null;
        for (File child : children) {
            if (!child.isDirectory()) continue;
            if (resolveLoginShellPath(child) != null) return child;
        }
        return null;
    }

    protected void ensureDistroBootstrap(String distro, File runtimeRoot, File rootfsDir) throws Exception {
        if (!requiresPythonBootstrap(distro)) return;
        if (distroHasPython(runtimeRoot, rootfsDir)) return;
        notifyProotProgress("distro", "extracting", "Installiere Python in Distro.", -1, -1, distro);
        ShellExecutionResult install = runInProot(
            runtimeRoot,
            rootfsDir,
            "set -e; "
                + "if [ ! -s /etc/resolv.conf ] || ! grep -Eq '^nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then "
                + "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf 2>/dev/null || true; "
                + "fi; "
                + "if command -v apt-get >/dev/null 2>&1; then export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y python3; else echo ANANTA_APT_MISSING; exit 2; fi; "
                + "if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then echo ANANTA_PY_OK; else echo ANANTA_PY_MISSING; exit 3; fi",
            600
        );
        String output = String.valueOf(install.output == null ? "" : install.output);
        if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_PY_OK")) {
            throw new IOException("Python bootstrap failed: " + output.trim());
        }
    }

    protected boolean requiresPythonBootstrap(String distro) {
        String normalized = String.valueOf(distro == null ? "" : distro).trim().toLowerCase();
        return "ubuntu".equals(normalized) || "debian".equals(normalized);
    }

    protected boolean distroHasPython(File runtimeRoot, File rootfsDir) throws Exception {
        ShellExecutionResult probe = runInProot(
            runtimeRoot,
            rootfsDir,
            "if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then echo ANANTA_PY_OK; else echo ANANTA_PY_MISSING; fi",
            120
        );
        String output = String.valueOf(probe.output == null ? "" : probe.output);
        return !probe.timedOut && probe.exitCode == 0 && output.contains("ANANTA_PY_OK");
    }

    protected boolean probeInProot(File runtimeRoot, File rootfsDir, String command) {
        try {
            ShellExecutionResult probe = runInProot(runtimeRoot, rootfsDir, command, 180);
            String output = String.valueOf(probe.output == null ? "" : probe.output);
            return !probe.timedOut && probe.exitCode == 0 && output.contains("ANANTA_OK");
        } catch (Exception ignored) {
            return false;
        }
    }

    protected ShellExecutionResult runInProot(File runtimeRoot, File rootfsDir, String innerCommand, int timeoutSeconds) throws Exception {
        String runtimePath = runtimeRoot.getAbsolutePath();
        String rootfsPath = rootfsDir.getAbsolutePath();
        String wrappedInnerCommand =
            "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                + "export HOME=/root; "
                + "export TERM=xterm-256color; "
                + String.valueOf(innerCommand == null ? "" : innerCommand).trim();
        String command = ""
            + "ANANTA_PROOT_RUNTIME=" + shQuote(runtimePath) + "; "
            + "ANANTA_ROOTFS=" + shQuote(rootfsPath) + "; "
            + "ANANTA_PROOT_WRAPPER=\"$ANANTA_PROOT_RUNTIME/bin/proot\"; "
            + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; "
            + "mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "chmod 700 \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "PROOT_FORCE_KOMPAT=1 GLIBC_TUNABLES=glibc.pthread.rseq=0 "
            + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color "
            + "/system/bin/sh \"$ANANTA_PROOT_WRAPPER\" "
            + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" "
            + "-w / /bin/sh -c " + shQuote(wrappedInnerCommand);
        return executeShellCommand(command, timeoutSeconds);
    }

    protected String shQuote(String value) {
        String text = String.valueOf(value == null ? "" : value);
        return "'" + text.replace("'", "'\"'\"'") + "'";
    }

    protected String resolveLoginShellPath(File rootfsDir) {
        if (rootfsDir == null || !rootfsDir.isDirectory()) return null;
        String[] candidates = {
            "/usr/bin/bash", "/usr/bin/dash", "/usr/bin/sh",
            "/bin/bash", "/bin/sh", "/bin/dash", "/bin/ash"
        };
        for (String candidate : candidates) {
            File file = new File(rootfsDir, candidate.startsWith("/") ? candidate.substring(1) : candidate);
            if (file.isFile()) return candidate;
        }
        return null;
    }

    protected void extractTarStreamToDirectory(InputStream input, File targetDir) throws IOException {
        byte[] header = new byte[512];
        while (readFully(input, header, 0, header.length)) {
            if (isZeroBlock(header)) break;
            String entryName = tarEntryName(header);
            long size = parseTarOctal(header, 124, 12);
            int mode = (int) parseTarOctal(header, 100, 8);
            char type = (char) (header[156] & 0xff);
            String linkName = readTarString(header, 157, 100);

            // Skip root marker entries and metadata-only tar records (pax/gnu extensions).
            if (isRootMarkerEntry(entryName) || isMetadataOnlyEntry(type)) {
                skipFully(input, size);
                skipFully(input, (512 - (size % 512)) % 512);
                continue;
            }

            if (isDirectoryEntry(type, entryName)) {
                File outFile = secureTarTarget(targetDir, entryName);
                if (!outFile.exists() && !outFile.mkdirs()) {
                    throw new IOException("Could not create directory: " + outFile.getAbsolutePath());
                }
            } else if (type == '2') {
                File outFile = secureTarTarget(targetDir, entryName);
                ensureParent(outFile);
                createSymlink(outFile, linkName);
            } else if (type == 0 || type == '0') {
                File outFile = secureTarTarget(targetDir, entryName);
                if (outFile.isDirectory()) {
                    skipFully(input, size);
                    skipFully(input, (512 - (size % 512)) % 512);
                    continue;
                }
                ensureParent(outFile);
                try (FileOutputStream output = new FileOutputStream(outFile, false)) {
                    copyFixedBytes(input, output, size);
                }
                applyMode(outFile, mode);
            } else {
                skipFully(input, size);
            }
            skipFully(input, (512 - (size % 512)) % 512);
        }
    }

    protected boolean isMetadataOnlyEntry(char type) {
        return type == 'x' || type == 'g' || type == 'L' || type == 'K';
    }

    protected boolean isRootMarkerEntry(String entryName) {
        String normalized = String.valueOf(entryName == null ? "" : entryName).replace('\\', '/').trim();
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        return normalized.isEmpty() || ".".equals(normalized) || "./".equals(normalized);
    }

    protected boolean isDirectoryEntry(char type, String entryName) {
        if (type == '5') return true;
        String name = String.valueOf(entryName == null ? "" : entryName).trim();
        return !name.isEmpty() && name.endsWith("/");
    }

    protected File secureTarTarget(File targetDir, String entryName) throws IOException {
        String normalized = String.valueOf(entryName == null ? "" : entryName).replace('\\', '/');
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        if (normalized.isEmpty()) {
            throw new IOException("Invalid tar entry name.");
        }
        File out = new File(targetDir, normalized);
        Path targetPath = out.getCanonicalFile().toPath();
        Path rootPath = targetDir.getCanonicalFile().toPath();
        if (!targetPath.startsWith(rootPath)) {
            throw new IOException("Blocked path traversal in tar entry: " + entryName);
        }
        return out;
    }

    protected void ensureParent(File file) throws IOException {
        File parent = file.getParentFile();
        if (parent == null) return;
        if (parent.exists()) return;
        if (!parent.mkdirs()) {
            throw new IOException("Could not create parent directory: " + parent.getAbsolutePath());
        }
    }

    protected void copyFixedBytes(InputStream input, FileOutputStream output, long bytes) throws IOException {
        long remaining = bytes;
        byte[] buffer = new byte[8192];
        while (remaining > 0) {
            int read = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
            if (read == -1) throw new IOException("Unexpected EOF while reading tar payload.");
            output.write(buffer, 0, read);
            remaining -= read;
        }
        output.flush();
    }

    protected void copyStream(InputStream input, OutputStream output) throws IOException {
        byte[] buffer = new byte[8192];
        int read;
        while ((read = input.read(buffer)) != -1) {
            output.write(buffer, 0, read);
        }
        output.flush();
    }

    protected void applyMode(File file, int mode) {
        if ((mode & 0400) != 0) file.setReadable(true, true);
        if ((mode & 0004) != 0) file.setReadable(true, false);
        if ((mode & 0200) != 0) file.setWritable(true, true);
        if ((mode & 0002) != 0) file.setWritable(true, false);
        if ((mode & 0100) != 0 || (mode & 0010) != 0 || (mode & 0001) != 0) {
            file.setExecutable(true, false);
        }
    }

    protected void createSymlink(File linkFile, String linkTarget) throws IOException {
        if (linkTarget == null || linkTarget.isBlank()) return;
        Path linkPath = linkFile.toPath();
        try {
            Files.deleteIfExists(linkPath);
            Files.createSymbolicLink(linkPath, Paths.get(linkTarget));
        } catch (UnsupportedOperationException ignored) {
            // Some Android filesystems may not support symbolic links for app users.
        }
    }

    protected void clearDirectory(File directory) throws IOException {
        File[] entries = directory.listFiles();
        if (entries == null) return;
        for (File entry : entries) {
            if (entry.isDirectory()) clearDirectory(entry);
            if (!entry.delete()) {
                throw new IOException("Could not delete " + entry.getAbsolutePath());
            }
        }
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

    protected String tarEntryName(byte[] header) {
        String name = readTarString(header, 0, 100);
        String prefix = readTarString(header, 345, 155);
        if (prefix.isEmpty()) return name;
        if (name.isEmpty()) return prefix;
        return prefix + "/" + name;
    }

    protected String readTarString(byte[] buffer, int offset, int len) {
        int end = offset;
        while (end < offset + len && buffer[end] != 0) end += 1;
        return new String(buffer, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    protected long parseTarOctal(byte[] buffer, int offset, int len) {
        // GNU tar may encode numeric fields in base-256 when values exceed octal field width.
        if ((buffer[offset] & 0x80) != 0) {
            long value = buffer[offset] & 0x7fL;
            for (int i = 1; i < len; i++) {
                value = (value << 8) | (buffer[offset + i] & 0xffL);
            }
            return value;
        }
        String raw = readTarString(buffer, offset, len);
        if (raw.isEmpty()) return 0L;
        try {
            return Long.parseLong(raw.trim(), 8);
        } catch (NumberFormatException ignored) {
            return 0L;
        }
    }

    protected String baseName(String path) {
        if (path == null || path.isBlank()) return "";
        String normalized = path.replace('\\', '/');
        int idx = normalized.lastIndexOf('/');
        if (idx < 0) return normalized;
        return normalized.substring(idx + 1);
    }

    protected void skipFully(InputStream input, long bytes) throws IOException {
        long remaining = bytes;
        while (remaining > 0) {
            long skipped = input.skip(remaining);
            if (skipped <= 0) {
                if (input.read() == -1) throw new IOException("Unexpected EOF while skipping.");
                skipped = 1;
            }
            remaining -= skipped;
        }
    }

    protected void copyFile(File source, File target) throws IOException {
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

    protected String computeSha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        }
        byte[] hash = digest.digest();
        StringBuilder hex = new StringBuilder(hash.length * 2);
        for (byte value : hash) {
            String part = Integer.toHexString(0xff & value);
            if (part.length() == 1) hex.append('0');
            hex.append(part);
        }
        return hex.toString();
    }

    protected String readProcessOutput(InputStream stream, int maxChars) throws IOException {
        StringBuilder out = new StringBuilder();
        boolean truncated = false;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            char[] buffer = new char[4096];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                int remaining = maxChars - out.length();
                if (remaining > 0) {
                    int toAppend = Math.min(remaining, read);
                    out.append(buffer, 0, toAppend);
                }
                if (out.length() >= maxChars) {
                    truncated = true;
                }
            }
        }
        if (truncated) {
            out.append("\n[ananta-mobile-shell] Output truncated");
        }
        return out.toString().trim();
    }

}
