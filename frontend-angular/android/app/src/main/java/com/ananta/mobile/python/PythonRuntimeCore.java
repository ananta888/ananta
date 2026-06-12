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

abstract class PythonRuntimeCore extends Plugin {
    protected static final int DEFAULT_SHELL_TIMEOUT_SECONDS = 20;
    protected static final int MAX_SHELL_TIMEOUT_SECONDS = 1800;
    protected static final int MAX_SHELL_OUTPUT_CHARS = 120_000;
    protected static final int MAX_SESSION_OUTPUT_CHARS = 200_000;
    protected static final String PROOT_RUNTIME_SUBDIR = "proot-runtime";
    protected static final String PROOT_BIN_FILE = "proot-rs";
    protected static final String PROOT_CLASSIC_FILE = "proot-classic";
    protected static final String PROOT_WRAPPER_FILE = "proot";
    protected static final String PROOT_CLASSIC_EMBEDDED_LIB_FILE = "libprootclassic.so";
    protected static final String PROOT_EMBEDDED_LIB_FILE = "libprootrs.so";
    protected static final String PROOT_RS_RELEASE_URL = "https://github.com/proot-me/proot-rs/releases/download/v0.1.0/proot-rs-v0.1.0-aarch64-linux-android.tar.gz";
    protected static final String PROOT_CLASSIC_RELEASE_URL = "https://github.com/proot-me/proot/releases/download/v5.3.0/proot-v5.3.0-aarch64-static";
    protected static final String PROOT_DISTRO_RELEASE_API = "https://api.github.com/repos/termux/proot-distro/releases/latest";
    protected static final String PROOT_DISTRO_PLUGIN_BASE = "https://raw.githubusercontent.com/termux/proot-distro/master/distro-plugins/";
    protected static final String ANANTA_REPO_URL = "https://github.com/ananta888/ananta/archive/refs/heads/main.tar.gz";
    protected static final String OPENCODE_VERSION = "v0.0.55";
    protected static final String OPENCODE_URL = "https://github.com/opencode-ai/opencode/releases/download/" + OPENCODE_VERSION + "/opencode-linux-arm64.tar.gz";
    protected static final String UBUNTU_ROOTFS_PRESEED_ASSET = "proot-seed/ubuntu-rootfs.tar.xz";
    protected static final String UBUNTU_ROOTFS_PRESEED_VERSION_ASSET = "proot-seed/ubuntu-rootfs.version";
    protected static final String ANANTA_WORKSPACE_PRESEED_ASSET = "proot-seed/ananta-workspace.tar.xz";
    protected static final String ANANTA_WORKSPACE_PRESEED_VERSION_ASSET = "proot-seed/ananta-workspace.version";
    protected static final String ROOTFS_PRESEED_MARKER_FILE = ".ananta-preseed-version";

    protected static final int PROXY_PORT = 18080;

    protected final ExecutorService worker = Executors.newSingleThreadExecutor();
    protected final Map<String, ShellSession> shellSessions = new ConcurrentHashMap<>();
    protected final HttpConnectProxy httpProxy = new HttpConnectProxy(PROXY_PORT);
    protected volatile boolean hubRunning;
    protected volatile boolean workerRunning;
    protected volatile String lastError;
    protected static final class ShellExecutionResult {
        final String output;
        final int exitCode;
        final boolean timedOut;

        ShellExecutionResult(String output, int exitCode, boolean timedOut) {
            this.output = output;
            this.exitCode = exitCode;
            this.timedOut = timedOut;
        }
    }

    protected static final class DistroDownloadMeta {
        final String url;
        final String sha256;

        DistroDownloadMeta(String url, String sha256) {
            this.url = url;
            this.sha256 = sha256;
        }
    }

    protected static final class ProotProbeResult {
        final boolean runnable;
        final String message;

        ProotProbeResult(boolean runnable, String message) {
            this.runnable = runnable;
            this.message = String.valueOf(message == null ? "" : message).trim();
        }
    }

    protected static final class ShellSessionRead {
        final String output;
        final boolean hasMore;

        ShellSessionRead(String output, boolean hasMore) {
            this.output = output;
            this.hasMore = hasMore;
        }
    }

    protected static final class ShellSession {
        protected final Process process;
        protected final BufferedWriter stdin;
        protected final StringBuilder output = new StringBuilder();
        protected final Object outputLock = new Object();
        protected volatile int readOffset = 0;

        ShellSession(Process process) {
            this.process = process;
            this.stdin = new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
        }

        void startReaderThread() {
            Thread reader = new Thread(() -> {
                try (BufferedReader br = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                    char[] buffer = new char[2048];
                    int read;
                    while ((read = br.read(buffer)) != -1) {
                        appendOutput(new String(buffer, 0, read));
                    }
                } catch (IOException ignored) {
                    // stream closed by process termination
                }
            }, "ananta-shell-session-reader");
            reader.setDaemon(true);
            reader.start();
        }

        void write(String input) throws IOException {
            // Translate CR to LF (no TTY driver to perform ICRNL)
            stdin.write(input.replace("\r\n", "\n").replace("\r", "\n"));
            stdin.flush();
        }

        void interrupt() throws IOException {
            long pid = resolveProcessPid(process);
            Process signal = null;
            try {
                if (pid <= 0) {
                    process.destroy();
                    return;
                }
                String pidText = Long.toString(pid);
                signal = new ProcessBuilder(
                    "/system/bin/sh",
                    "-c",
                    "kill -INT -" + pidText + " >/dev/null 2>&1 || kill -INT " + pidText + " >/dev/null 2>&1"
                ).redirectErrorStream(true).start();
                signal.waitFor(2, TimeUnit.SECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                if (signal != null) signal.destroy();
            }
        }

        private long resolveProcessPid(Process target) {
            try {
                Field pidField = target.getClass().getDeclaredField("pid");
                pidField.setAccessible(true);
                Object value = pidField.get(target);
                if (value instanceof Number) return ((Number) value).longValue();
            } catch (Exception ignored) {
                // Fallback below.
            }
            return -1L;
        }

        ShellSessionRead readDelta(int maxChars) {
            synchronized (outputLock) {
                // If no data yet but process is alive, briefly wait for output
                if (readOffset >= output.length() && process.isAlive()) {
                    try {
                        outputLock.wait(15);
                    } catch (InterruptedException ignored) {
                        Thread.currentThread().interrupt();
                    }
                }
                if (readOffset >= output.length()) {
                    return new ShellSessionRead("", false);
                }
                int available = output.length() - readOffset;
                int toRead = Math.min(available, maxChars);
                String chunk = output.substring(readOffset, readOffset + toRead);
                readOffset += toRead;
                boolean hasMore = readOffset < output.length();
                return new ShellSessionRead(chunk, hasMore);
            }
        }

        boolean isRunning() {
            return process.isAlive();
        }

        int exitCode() {
            return process.isAlive() ? -1 : process.exitValue();
        }

        void close() {
            try {
                stdin.write("exit\n");
                stdin.flush();
            } catch (IOException ignored) {
            }
            process.destroy();
            if (process.isAlive()) {
                process.destroyForcibly();
            }
        }

        private void appendOutput(String text) {
            if (text == null || text.isEmpty()) return;
            synchronized (outputLock) {
                output.append(text);
                int overflow = output.length() - MAX_SESSION_OUTPUT_CHARS;
                if (overflow > 0) {
                    output.delete(0, overflow);
                    readOffset = Math.max(0, readOffset - overflow);
                }
                outputLock.notifyAll();
            }
        }
    }
}
