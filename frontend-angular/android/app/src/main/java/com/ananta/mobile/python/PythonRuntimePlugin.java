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
@CapacitorPlugin(name = "PythonRuntime")
public class PythonRuntimePlugin extends PythonRuntimeArchiveSupport {
    @PluginMethod
    public void getRuntimeStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("pythonAvailable", isPythonAvailable());
        result.put("hubRunning", hubRunning);
        result.put("workerRunning", workerRunning);
        result.put("lastError", lastError);
        result.put("proxyRunning", httpProxy.isRunning());
        result.put("proxyPort", PROXY_PORT);
        call.resolve(result);
    }
    @PluginMethod
    public void getMobilePolicySignals(PluginCall call) {
        JSObject result = new JSObject();
        try {
            File dataDir = getContext().getFilesDir();
            long freeBytes = dataDir != null ? dataDir.getUsableSpace() : -1L;
            long totalBytes = dataDir != null ? dataDir.getTotalSpace() : -1L;
            result.put("wifi", "unknown");
            result.put("charging", "unknown");
            result.put("storage", "unknown");
            result.put("storageFreeBytes", freeBytes);
            result.put("storageTotalBytes", totalBytes);
            result.put("note", "Signals are explicit unknown unless runtime bridge provides device telemetry.");
            call.resolve(result);
        } catch (Exception error) {
            call.reject("Mobile policy signal check failed: " + error.getMessage());
        }
    }
    @PluginMethod
    public void startHub(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("start_hub");
                hubRunning = true;
                JSObject result = new JSObject();
                result.put("hubRunning", true);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Hub start failed: " + error.getMessage());
            }
        });
    }
    @PluginMethod
    public void stopHub(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("stop_hub");
                hubRunning = false;
                JSObject result = new JSObject();
                result.put("hubRunning", false);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Hub stop failed: " + error.getMessage());
            }
        });
    }
    @PluginMethod
    public void startWorker(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("start_worker");
                workerRunning = true;
                JSObject result = new JSObject();
                result.put("workerRunning", true);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Worker start failed: " + error.getMessage());
            }
        });
    }
    @PluginMethod
    public void stopWorker(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("stop_worker");
                workerRunning = false;
                JSObject result = new JSObject();
                result.put("workerRunning", false);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Worker stop failed: " + error.getMessage());
            }
        });
    }
    @PluginMethod
    public void runHealthCheck(PluginCall call) {
        worker.execute(() -> {
            try {
                String value = invokePython("health_check");
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("message", value);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Health check failed: " + error.getMessage());
            }
        });
    }
    @PluginMethod
    public void runShellCommand(PluginCall call) {
        String command = String.valueOf(call.getString("command", "")).trim();
        if (command.isEmpty()) {
            call.reject("command is required");
            return;
        }
        int timeoutSeconds = call.getInt("timeoutSeconds", DEFAULT_SHELL_TIMEOUT_SECONDS);
        if (timeoutSeconds < 1 || timeoutSeconds > MAX_SHELL_TIMEOUT_SECONDS) {
            call.reject("timeoutSeconds must be between 1 and " + MAX_SHELL_TIMEOUT_SECONDS);
            return;
        }
        worker.execute(() -> {
            try {
                ShellExecutionResult exec = executeShellCommand(command, timeoutSeconds);
                JSObject result = new JSObject();
                result.put("output", exec.output);
                result.put("exitCode", exec.exitCode);
                result.put("timedOut", exec.timedOut);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell execution failed: " + error.getMessage());
            }
        });
    }
    @PluginMethod
    public void openShellSession(PluginCall call) {
        String cwd = String.valueOf(call.getString("cwd", "")).trim();
        String initialCommand = String.valueOf(call.getString("initialCommand", "")).trim();
        String shell = String.valueOf(call.getString("shell", "sh")).trim();
        if (shell.isEmpty()) shell = "sh";
        // Auto-start HTTP proxy for proot network access
        ensureProxyRunning();
        final String selectedShell = shell;
        final String selectedCwd = cwd;
        final String selectedInitialCommand = initialCommand;
        worker.execute(() -> {
            try {
                File workingDir = resolveShellWorkingDirectory(selectedCwd);
                ProcessBuilder builder;
                if (!selectedInitialCommand.isEmpty()) {
                    // Use -c to pass the command as argument instead of writing to stdin.
                    // This matches executeShellCommand behavior where proot works correctly.
                    builder = new ProcessBuilder("/system/bin/sh", "-c", selectedInitialCommand);
                } else {
                    builder = new ProcessBuilder(selectedShell);
                }
                builder.directory(workingDir);
                applyShellEnvironment(builder, workingDir);
                Process process = builder.redirectErrorStream(true).start();
                ShellSession session = new ShellSession(process);
                String sessionId = UUID.randomUUID().toString();
                shellSessions.put(sessionId, session);
                session.startReaderThread();
                JSObject result = new JSObject();
                result.put("sessionId", sessionId);
                result.put("running", session.isRunning());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell session start failed: " + error.getMessage());
            }
        });
    }
    protected void ensureProxyRunning() {
        if (httpProxy.isRunning()) return;
        try {
            httpProxy.start();
        } catch (IOException e) {
            // Log but don't block shell session
            lastError = "HTTP proxy start failed: " + e.getMessage();
        }
    }
    @PluginMethod
    public void writeShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        String input = String.valueOf(call.getString("input", ""));
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        ShellSession session = shellSessions.get(sessionId);
        if (session == null) {
            call.reject("Shell session not found");
            return;
        }
        worker.execute(() -> {
            try {
                session.write(input);
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("running", session.isRunning());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell write failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void readShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        int maxChars = call.getInt("maxChars", 8000);
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        if (maxChars < 256) maxChars = 256;
        if (maxChars > 32000) maxChars = 32000;
        ShellSession session = shellSessions.get(sessionId);
        if (session == null) {
            call.reject("Shell session not found");
            return;
        }

        final int maxCharsFinal = maxChars;
        worker.execute(() -> {
            try {
                ShellSessionRead read = session.readDelta(maxCharsFinal);
                JSObject result = new JSObject();
                result.put("output", read.output);
                result.put("hasMore", read.hasMore);
                result.put("running", session.isRunning());
                if (!session.isRunning()) {
                    result.put("exitCode", session.exitCode());
                }
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell read failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void interruptShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        ShellSession session = shellSessions.get(sessionId);
        if (session == null) {
            call.reject("Shell session not found");
            return;
        }
        worker.execute(() -> {
            try {
                session.interrupt();
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("running", session.isRunning());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell interrupt failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void closeShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        ShellSession session = shellSessions.remove(sessionId);
        if (session == null) {
            JSObject result = new JSObject();
            result.put("closed", true);
            call.resolve(result);
            return;
        }

        worker.execute(() -> {
            session.close();
            JSObject result = new JSObject();
            result.put("closed", true);
            call.resolve(result);
        });
    }

    @PluginMethod
    public void getProotRuntimeStatus(PluginCall call) {
        worker.execute(() -> {
            try {
                JSObject result = new JSObject();
                File runtimeRoot = runtimeRootDir();
                File prootWrapper = new File(runtimeRoot, "bin/" + PROOT_WRAPPER_FILE);
                File classicProotBinary = new File(runtimeRoot, "bin/" + PROOT_CLASSIC_FILE);
                File runtimeProotBinary = new File(runtimeRoot, "bin/" + PROOT_BIN_FILE);
                File embeddedClassicBinary = embeddedNativeClassicProotBinary();
                File embeddedProotBinary = embeddedNativeProotBinary();
                File selectedProotBinary = resolveProotBinaryCandidate(
                    embeddedClassicBinary,
                    embeddedProotBinary,
                    classicProotBinary,
                    runtimeProotBinary
                );
                File distroRoot = new File(runtimeRoot, "distros");
                JSArray distros = new JSArray();
                File[] entries = distroRoot.listFiles();
                if (entries != null) {
                    for (File entry : entries) {
                        if (!entry.isDirectory()) continue;
                        File rootfs = new File(entry, "rootfs");
                        if (!rootfs.isDirectory()) continue;
                        JSObject item = new JSObject();
                        item.put("name", entry.getName());
                        item.put("rootfsPath", rootfs.getAbsolutePath());
                        distros.put(item);
                    }
                }

                result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                result.put("prootPath", prootWrapper.getAbsolutePath());
                result.put("prootBinaryPath", selectedProotBinary != null ? selectedProotBinary.getAbsolutePath() : "");
                result.put("prootBinarySource", resolveProotBinarySource(
                    selectedProotBinary,
                    embeddedClassicBinary,
                    embeddedProotBinary,
                    classicProotBinary
                ));
                boolean prootExists = prootWrapper.exists() && selectedProotBinary != null && selectedProotBinary.exists();
                result.put("prootExists", prootExists);
                ProotProbeResult probe = new ProotProbeResult(false, "Runtime nicht installiert.");
                if (prootExists) {
                    prootWrapper.setReadable(true, false);
                    selectedProotBinary.setReadable(true, false);
                    prootWrapper.setExecutable(true, false);
                    selectedProotBinary.setExecutable(true, false);
                    createProotWrapper(new File(runtimeRoot, "bin"), selectedProotBinary);
                    probe = probeProotWrapper(prootWrapper);
                }
                result.put("prootExecutable", prootExists && probe.runnable);
                result.put("prootProbeMessage", probe.message);
                result.put("distros", distros);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Could not read proot runtime status: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installProotRuntime(PluginCall call) {
        worker.execute(() -> {
            try {
                String url = String.valueOf(call.getString("prootUrl", PROOT_RS_RELEASE_URL)).trim();
                if (url.isEmpty()) url = PROOT_RS_RELEASE_URL;
                String classicUrl = String.valueOf(call.getString("prootClassicUrl", PROOT_CLASSIC_RELEASE_URL)).trim();
                if (classicUrl.isEmpty()) classicUrl = PROOT_CLASSIC_RELEASE_URL;
                notifyProotProgress("runtime", "preparing", "Runtime-Installation gestartet.", -1, -1, null);

                File runtimeRoot = runtimeRootDir();
                File binDir = ensureDir(runtimeRoot, "bin");
                File tmpDir = ensureDir(runtimeRoot, "tmp");
                File embeddedClassicBinary = embeddedNativeClassicProotBinary();
                if (isUsableBinary(embeddedClassicBinary)) {
                    createProotWrapper(binDir, embeddedClassicBinary);
                    ProotProbeResult probe = probeProotWrapper(new File(binDir, PROOT_WRAPPER_FILE));
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                    result.put("prootBinaryPath", embeddedClassicBinary.getAbsolutePath());
                    result.put("prootBinarySource", "embedded-classic-native-lib");
                    result.put("alreadyInstalled", true);
                    result.put("runnable", probe.runnable);
                    result.put("probeMessage", probe.message);
                    String doneMessage = probe.runnable
                        ? "Runtime bereit (APK-embedded classic proot)."
                        : "Runtime vorhanden (APK-embedded classic proot), aber nicht startbar.";
                    notifyProotProgress("runtime", "done", doneMessage, -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File classicBinary = new File(binDir, PROOT_CLASSIC_FILE);
                if (!isUsableBinary(classicBinary)) {
                    File classicDownload = new File(tmpDir, "proot-classic-aarch64-static");
                    notifyProotProgress("runtime", "downloading", "Klassische proot Runtime wird geladen.", 0, -1, null);
                    downloadToFile(classicUrl, classicDownload, "runtime");
                    copyFile(classicDownload, classicBinary);
                    if (!classicBinary.setExecutable(true, false)) {
                        throw new IOException("Could not mark classic proot binary executable.");
                    }
                }
                createProotWrapper(binDir, classicBinary);
                ProotProbeResult classicProbe = probeProotWrapper(new File(binDir, PROOT_WRAPPER_FILE));
                if (classicProbe.runnable) {
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                    result.put("prootBinaryPath", classicBinary.getAbsolutePath());
                    result.put("prootBinarySource", "classic-static");
                    result.put("alreadyInstalled", true);
                    result.put("runnable", true);
                    result.put("probeMessage", classicProbe.message);
                    notifyProotProgress("runtime", "done", "Runtime bereit (klassisches proot).", -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File embeddedProotBinary = embeddedNativeProotBinary();
                if (isUsableBinary(embeddedProotBinary)) {
                    createProotWrapper(binDir, embeddedProotBinary);
                    ProotProbeResult probe = probeProotWrapper(new File(binDir, PROOT_WRAPPER_FILE));
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                    result.put("prootBinaryPath", embeddedProotBinary.getAbsolutePath());
                    result.put("prootBinarySource", "embedded-native-lib");
                    result.put("alreadyInstalled", true);
                    result.put("runnable", probe.runnable);
                    result.put("probeMessage", probe.message);
                    String doneMessage = probe.runnable
                        ? "Runtime bereit (APK-native binary)."
                        : "Runtime vorhanden (APK-native binary), aber nicht startbar.";
                    notifyProotProgress("runtime", "done", doneMessage, -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File existingWrapper = new File(binDir, PROOT_WRAPPER_FILE);
                File existingBinary = new File(binDir, PROOT_BIN_FILE);
                if (existingWrapper.exists() && existingBinary.exists()) {
                    existingWrapper.setReadable(true, false);
                    existingBinary.setReadable(true, false);
                    existingWrapper.setExecutable(true, false);
                    existingBinary.setExecutable(true, false);
                    ProotProbeResult probe = probeProotWrapper(existingWrapper);
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", existingWrapper.getAbsolutePath());
                    result.put("alreadyInstalled", true);
                    result.put("runnable", probe.runnable);
                    result.put("probeMessage", probe.message);
                    String doneMessage = probe.runnable
                        ? "Runtime bereits installiert."
                        : "Runtime bereits installiert, aber nicht startbar.";
                    notifyProotProgress("runtime", "done", doneMessage, -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File downloadTarget = new File(tmpDir, "proot-rs.tar.gz");
                downloadToFile(url, downloadTarget, "runtime");
                notifyProotProgress("runtime", "extracting", "Runtime wird entpackt.", -1, -1, null);

                File extractedBinary = extractFirstExecutableFromTarGz(downloadTarget, tmpDir, "proot-rs");
                if (extractedBinary == null || !extractedBinary.exists()) {
                    throw new IOException("No executable proot-rs binary found in archive.");
                }

                File targetBinary = new File(binDir, PROOT_BIN_FILE);
                copyFile(extractedBinary, targetBinary);
                if (!targetBinary.setExecutable(true, false)) {
                    throw new IOException("Could not mark proot binary executable.");
                }
                createProotWrapper(binDir, targetBinary);

                JSObject result = new JSObject();
                result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                notifyProotProgress("runtime", "done", "Runtime installiert.", -1, -1, null);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("runtime", "error", error.getMessage(), -1, -1, null);
                call.reject("Proot runtime installation failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installProotDistro(PluginCall call) {
        String distro = String.valueOf(call.getString("distro", "")).trim().toLowerCase();
        if (distro.isEmpty()) {
            call.reject("distro is required");
            return;
        }

        worker.execute(() -> {
            try {
                notifyProotProgress("distro", "preparing", "Distro-Installation gestartet.", -1, -1, distro);
                ensureProotInstalled();
                File runtimeRoot = runtimeRootDir();
                File distrosDir = ensureDir(runtimeRoot, "distros");
                File distroDir = ensureDir(distrosDir, distro);
                File rootfsDir = ensureDir(distroDir, "rootfs");
                File existingRootfs = resolveInstalledRootfs(rootfsDir);
                if (existingRootfs != null) {
                    ensureDistroBootstrap(distro, runtimeRoot, existingRootfs);
                    JSObject result = new JSObject();
                    result.put("distro", distro);
                    result.put("rootfsPath", existingRootfs.getAbsolutePath());
                    result.put("alreadyInstalled", true);
                    notifyProotProgress("distro", "done", "Distro bereits installiert.", -1, -1, distro);
                    call.resolve(result);
                    return;
                }
                String bundledVersion = installBundledDistroIfAvailable(distro, rootfsDir);
                if (bundledVersion != null) {
                    File installedRootfs = resolveInstalledRootfs(rootfsDir);
                    if (installedRootfs == null) {
                        throw new IOException("Bundled distro extraction completed, but no usable rootfs was detected.");
                    }
                    writeTextFile(new File(installedRootfs, ROOTFS_PRESEED_MARKER_FILE), bundledVersion);
                    ensureDistroBootstrap(distro, runtimeRoot, installedRootfs);

                    JSObject result = new JSObject();
                    result.put("distro", distro);
                    result.put("rootfsPath", installedRootfs.getAbsolutePath());
                    result.put("source", "bundled-apk-asset");
                    result.put("preseedVersion", bundledVersion);
                    notifyProotProgress("distro", "done", "Gebuendelte Distro installiert.", -1, -1, distro);
                    call.resolve(result);
                    return;
                }
                DistroDownloadMeta downloadMeta = resolveDistroDownloadMeta(distro);
                String assetUrl = downloadMeta.url;
                if (assetUrl == null || assetUrl.isBlank()) {
                    throw new IOException("No aarch64 archive found for distro: " + distro);
                }
                File tmpDir = ensureDir(runtimeRoot, "tmp");
                File archive = new File(tmpDir, distro + "-rootfs.tar.xz");
                downloadToFile(assetUrl, archive, "distro", distro);
                notifyProotProgress("distro", "extracting", "Distro wird entpackt.", -1, -1, distro);
                if (downloadMeta.sha256 != null && !downloadMeta.sha256.isBlank()) {
                    String actualSha = computeSha256(archive);
                    if (!actualSha.equalsIgnoreCase(downloadMeta.sha256)) {
                        throw new IOException("SHA256 mismatch for distro archive.");
                    }
                }
                clearDirectory(rootfsDir);
                extractTarXzToDirectory(archive, rootfsDir);
                File installedRootfs = resolveInstalledRootfs(rootfsDir);
                if (installedRootfs == null) {
                    throw new IOException("Distro extraction completed, but no usable rootfs was detected.");
                }
                ensureDistroBootstrap(distro, runtimeRoot, installedRootfs);

                JSObject result = new JSObject();
                result.put("distro", distro);
                result.put("rootfsPath", installedRootfs.getAbsolutePath());
                notifyProotProgress("distro", "done", "Distro installiert.", -1, -1, distro);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("distro", "error", error.getMessage(), -1, -1, distro);
                call.reject("Distro installation failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void getGuidedSetupStatus(PluginCall call) {
        worker.execute(() -> {
            try {
                File runtimeRoot = runtimeRootDir();
                File prootWrapper = new File(runtimeRoot, "bin/" + PROOT_WRAPPER_FILE);
                File selectedBinary = resolveProotBinaryCandidate(
                    embeddedNativeClassicProotBinary(),
                    embeddedNativeProotBinary(),
                    new File(runtimeRoot, "bin/" + PROOT_CLASSIC_FILE),
                    new File(runtimeRoot, "bin/" + PROOT_BIN_FILE)
                );
                boolean runtimeInstalled = prootWrapper.exists() && selectedBinary != null && selectedBinary.exists();
                boolean runtimeReady = false;
                String runtimeMessage = "Runtime nicht installiert.";
                if (runtimeInstalled) {
                    createProotWrapper(new File(runtimeRoot, "bin"), selectedBinary);
                    ProotProbeResult probe = probeProotWrapper(prootWrapper);
                    runtimeReady = probe.runnable;
                    runtimeMessage = probe.message;
                }

                File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
                boolean ubuntuInstalled = ubuntuRootfs != null;
                boolean pythonReady = false;
                boolean pipReady = false;
                boolean libgompReady = false;
                boolean opencodeReady = false;
                boolean anantaCliReady = false;
                boolean anantaTuiReady = false;
                boolean workerCommandReady = false;
                boolean workerImportReady = false;
                String workerProbeMessage = "";

                File workspaceRoot = new File(getContext().getFilesDir(), "ananta");
                boolean workspaceInstalled = new File(workspaceRoot, "agent/ai_agent.py").isFile();

                if (runtimeReady && ubuntuInstalled) {
                    pythonReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v python3 >/dev/null 2>&1; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    pipReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v pip3 >/dev/null 2>&1 || python3 -m pip --version >/dev/null 2>&1; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    libgompReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if [ -f /lib/aarch64-linux-gnu/libgomp.so.1 ] || [ -f /usr/lib/aarch64-linux-gnu/libgomp.so.1 ]; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    opencodeReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v opencode >/dev/null 2>&1; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    anantaCliReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v ananta >/dev/null 2>&1 || [ -x /usr/local/bin/ananta ] || [ -x /home/ananta/.local/bin/ananta ] || [ -x /root/.local/bin/ananta ]; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    anantaTuiReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v ananta >/dev/null 2>&1; then ananta tui --help >/dev/null 2>&1 && echo ANANTA_OK || echo ANANTA_MISSING; elif [ -x /usr/local/bin/ananta ]; then /usr/local/bin/ananta tui --help >/dev/null 2>&1 && echo ANANTA_OK || echo ANANTA_MISSING; else echo ANANTA_MISSING; fi");
                    workerCommandReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v ananta-worker >/dev/null 2>&1 || [ -x /usr/local/bin/ananta-worker ] || [ -x /home/ananta/.local/bin/ananta-worker ] || [ -x /root/.local/bin/ananta-worker ]; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");

                    if (workspaceInstalled) {
                        File dataRoot = new File(getContext().getFilesDir(), "ananta-data");
                        String importCmd = "cd " + shQuote(workspaceRoot.getAbsolutePath())
                            + " && if [ -f ./agent/ai_agent.py ]; then PYTHONPATH="
                            + shQuote(workspaceRoot.getAbsolutePath())
                            + " DATA_DIR=" + shQuote(dataRoot.getAbsolutePath())
                            + " python3 -c \"from agent.ai_agent import create_app; print('ANANTA_WORKER_IMPORT_OK')\"; else echo ANANTA_WORKER_MISSING; exit 3; fi";
                        ShellExecutionResult importProbe = runInProot(runtimeRoot, ubuntuRootfs, importCmd, 120);
                        String out = String.valueOf(importProbe.output == null ? "" : importProbe.output);
                        workerImportReady = !importProbe.timedOut && importProbe.exitCode == 0 && out.contains("ANANTA_WORKER_IMPORT_OK");
                        workerProbeMessage = out.trim();
                    }
                }

                JSObject result = new JSObject();
                result.put("runtimeInstalled", runtimeInstalled);
                result.put("runtimeReady", runtimeReady);
                result.put("runtimeMessage", runtimeMessage);
                result.put("ubuntuInstalled", ubuntuInstalled);
                result.put("pythonReady", pythonReady);
                result.put("pipReady", pipReady);
                result.put("libgompReady", libgompReady);
                result.put("opencodeReady", opencodeReady);
                result.put("anantaCliReady", anantaCliReady);
                result.put("anantaTuiReady", anantaTuiReady);
                result.put("workerCommandReady", workerCommandReady);
                result.put("workspaceInstalled", workspaceInstalled);
                result.put("workerImportReady", workerImportReady);
                result.put("workerProbeMessage", workerProbeMessage);
                result.put("workspacePath", workspaceRoot.getAbsolutePath());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Could not read guided setup status: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installAnantaWorkspace(PluginCall call) {
        worker.execute(() -> {
            try {
                String repoUrl = String.valueOf(call.getString("repoUrl", ANANTA_REPO_URL)).trim();
                if (repoUrl.isEmpty()) repoUrl = ANANTA_REPO_URL;
                notifyProotProgress("workspace", "preparing", "Ananta-Workspace Installation gestartet.", -1, -1, "ubuntu");
                File runtimeRoot = runtimeRootDir();
                File workspaceRoot = new File(getContext().getFilesDir(), "ananta");
                String bundledVersion = installBundledWorkspaceIfAvailable(workspaceRoot);
                if (bundledVersion != null) {
                    File marker = new File(workspaceRoot, "agent/ai_agent.py");
                    if (!marker.isFile()) {
                        throw new IOException("Bundled workspace extraction incomplete: agent/ai_agent.py not found.");
                    }
                    ensureWorkspaceWorkerDependenciesIfPossible(runtimeRoot, workspaceRoot);
                    JSObject result = new JSObject();
                    result.put("workspacePath", workspaceRoot.getAbsolutePath());
                    result.put("repoUrl", "apk-asset:" + ANANTA_WORKSPACE_PRESEED_ASSET);
                    result.put("source", "bundled-apk-asset");
                    result.put("preseedVersion", bundledVersion);
                    notifyProotProgress("workspace", "done", "Gebuendelter Workspace installiert.", -1, -1, "ubuntu");
                    call.resolve(result);
                    return;
                }

                File tmpDir = ensureDir(runtimeRoot, "tmp");
                File archive = new File(tmpDir, "ananta-workspace.tar.gz");
                downloadToFile(repoUrl, archive, "workspace", "ubuntu");
                notifyProotProgress("workspace", "extracting", "Workspace wird entpackt.", -1, -1, "ubuntu");

                if (workspaceRoot.exists()) {
                    clearDirectory(workspaceRoot);
                } else if (!workspaceRoot.mkdirs()) {
                    throw new IOException("Could not create workspace directory: " + workspaceRoot.getAbsolutePath());
                }
                extractTarGzToDirectory(archive, workspaceRoot);
                File marker = new File(workspaceRoot, "agent/ai_agent.py");
                if (!marker.isFile()) {
                    throw new IOException("Workspace extraction incomplete: agent/ai_agent.py not found.");
                }
                ensureWorkspaceWorkerDependenciesIfPossible(runtimeRoot, workspaceRoot);
                JSObject result = new JSObject();
                result.put("workspacePath", workspaceRoot.getAbsolutePath());
                result.put("repoUrl", repoUrl);
                notifyProotProgress("workspace", "done", "Ananta-Workspace installiert.", -1, -1, "ubuntu");
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("workspace", "error", error.getMessage(), -1, -1, "ubuntu");
                call.reject("Workspace installation failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installWorkerDependencies(PluginCall call) {
        worker.execute(() -> {
            try {
                notifyProotProgress("worker", "preparing", "Worker-Dependencies werden installiert.", -1, -1, "ubuntu");
                File runtimeRoot = runtimeRootDir();
                File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
                if (ubuntuRootfs == null) {
                    throw new IOException("Ubuntu rootfs fehlt. Bitte zuerst Distro installieren.");
                }
                File workspaceRoot = new File(getContext().getFilesDir(), "ananta");
                File marker = new File(workspaceRoot, "agent/ai_agent.py");
                if (!marker.isFile()) {
                    throw new IOException("Workspace fehlt. Bitte zuerst installAnantaWorkspace ausfuehren.");
                }

                if (probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) {
                    JSObject result = new JSObject();
                    result.put("ok", true);
                    result.put("message", "Worker-Dependencies bereits vorhanden.");
                    result.put("source", "bundled-or-existing");
                    notifyProotProgress("worker", "done", "Worker-Dependencies bereits vorhanden.", -1, -1, "ubuntu");
                    call.resolve(result);
                    return;
                }

                notifyProotProgress("worker", "installing", "Fehlende Worker-Dependencies werden installiert.", -1, -1, "ubuntu");
                ShellExecutionResult install = runInProot(
                    runtimeRoot,
                    ubuntuRootfs,
                    buildWorkerDependencyInstallCommand(workspaceRoot, new File(getContext().getFilesDir(), "ananta-data")),
                    1200
                );
                String output = String.valueOf(install.output == null ? "" : install.output);
                if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_WORKER_DEPS_OK")) {
                    throw new IOException("Worker dependency install failed: " + output.trim());
                }
                if (!probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) {
                    throw new IOException("Worker dependency verification failed after install.");
                }
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("message", "Worker-Dependencies installiert.");
                result.put("source", "installed-in-proot");
                notifyProotProgress("worker", "done", "Worker-Dependencies installiert.", -1, -1, "ubuntu");
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("worker", "error", error.getMessage(), -1, -1, "ubuntu");
                call.reject("Worker dependency installation failed: " + error.getMessage());
            }
        });
    }

    protected void ensureWorkspaceWorkerDependenciesIfPossible(File runtimeRoot, File workspaceRoot) throws Exception {
        if (runtimeRoot == null || workspaceRoot == null) return;
        if (!new File(workspaceRoot, "agent/ai_agent.py").isFile()) return;
        File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
        if (ubuntuRootfs == null) return;
        if (probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) return;
        notifyProotProgress("worker", "installing", "Fehlende Worker-Dependencies werden fuer Workspace installiert.", -1, -1, "ubuntu");
        ShellExecutionResult install = runInProot(
            runtimeRoot,
            ubuntuRootfs,
            buildWorkerDependencyInstallCommand(workspaceRoot, new File(getContext().getFilesDir(), "ananta-data")),
            1200
        );
        String output = String.valueOf(install.output == null ? "" : install.output);
        if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_WORKER_DEPS_OK")) {
            throw new IOException("Worker dependency install failed: " + output.trim());
        }
        if (!probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) {
            throw new IOException("Worker dependency verification failed after install.");
        }
    }

    protected boolean probeWorkerDependenciesReady(File runtimeRoot, File ubuntuRootfs, File workspaceRoot) throws Exception {
        if (runtimeRoot == null || ubuntuRootfs == null || workspaceRoot == null) return false;
        if (!new File(workspaceRoot, "agent/ai_agent.py").isFile()) return false;
        File dataRoot = new File(getContext().getFilesDir(), "ananta-data");
        ShellExecutionResult probe = runInProot(
            runtimeRoot,
            ubuntuRootfs,
            ""
                + "ANANTA_WORKSPACE=" + shQuote(workspaceRoot.getAbsolutePath()) + "; "
                + "ANANTA_DATA_DIR=" + shQuote(dataRoot.getAbsolutePath()) + "; "
                + "mkdir -p \"$ANANTA_DATA_DIR\"; "
                + "if ! command -v python3 >/dev/null 2>&1; then echo ANANTA_MISSING_PYTHON; exit 2; fi; "
                + "if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then echo ANANTA_MISSING_PIP; exit 3; fi; "
                + "if ! command -v ananta-worker >/dev/null 2>&1 && [ ! -x /usr/local/bin/ananta-worker ] && [ ! -x /home/ananta/.local/bin/ananta-worker ] && [ ! -x /root/.local/bin/ananta-worker ]; then echo ANANTA_MISSING_WORKER_CMD; exit 5; fi; "
                + "DATA_DIR=\"$ANANTA_DATA_DIR\" PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" python3 -c \"from agent.ai_agent import create_app; print('ANANTA_WORKER_IMPORT_OK')\"; "
                + "echo ANANTA_WORKER_DEPS_READY",
            300
        );
        String output = String.valueOf(probe.output == null ? "" : probe.output);
        return !probe.timedOut && probe.exitCode == 0 && output.contains("ANANTA_WORKER_DEPS_READY") && output.contains("ANANTA_WORKER_IMPORT_OK");
    }

    protected String buildWorkerDependencyInstallCommand(File workspaceRoot, File dataRoot) {
        String workspacePath = workspaceRoot.getAbsolutePath();
        String dataPath = dataRoot.getAbsolutePath();
        return ""
            + "set -e; "
            + "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; "
            + "rm -f /etc/apt/apt.conf.d/99proxy 2>/dev/null || true; "
            + "if [ ! -s /etc/resolv.conf ] || ! grep -Eq '^nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then "
            + "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf 2>/dev/null || true; "
            + "fi; "
            + "export DEBIAN_FRONTEND=noninteractive; "
            + "MISSING_PACKAGES=''; "
            + "for bin in python3 git curl tar; do command -v \"$bin\" >/dev/null 2>&1 || MISSING_PACKAGES=\"$MISSING_PACKAGES $bin\"; done; "
            + "if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then MISSING_PACKAGES=\"$MISSING_PACKAGES python3-pip\"; fi; "
            + "if [ -n \"$MISSING_PACKAGES\" ]; then "
            + "if command -v apt-get >/dev/null 2>&1; then "
            + "apt-get update; "
            + "apt-get install -y --no-install-recommends ca-certificates $MISSING_PACKAGES; "
            + "else echo ANANTA_APT_MISSING:$MISSING_PACKAGES; exit 2; fi; "
            + "fi; "
            + "ANANTA_WORKSPACE=" + shQuote(workspacePath) + "; "
            + "ANANTA_DATA_DIR=" + shQuote(dataPath) + "; "
            + "mkdir -p \"$ANANTA_DATA_DIR\"; "
            + "if [ ! -f \"$ANANTA_WORKSPACE/pyproject.toml\" ]; then echo ANANTA_WORKSPACE_MISSING; exit 4; fi; "
            + "python3 -m pip install --break-system-packages --ignore-installed --no-input --progress-bar off "
            + "flask requests flask-cors pydantic pydantic-settings python-dotenv prometheus-client pyjwt "
            + "portalocker psutil sqlmodel typer click gitpython flask-sock simple-websocket "
            + "pypdf python-docx openpyxl python-pptx; "
            + "python3 -m pip install --break-system-packages --ignore-installed --no-input --progress-bar off -e \"$ANANTA_WORKSPACE\"; "
            + "export PATH=\"/home/ananta/.local/bin:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH\"; "
            + "if ! command -v ananta >/dev/null 2>&1; then "
            + "mkdir -p /usr/local/bin /home/ananta/.local/bin /root/.local/bin 2>/dev/null || true; "
            + "for target in /usr/local/bin/ananta /home/ananta/.local/bin/ananta /root/.local/bin/ananta; do "
            + "cat >\"$target\" <<'EOF'\n"
            + "#!/bin/sh\n"
            + "ANANTA_WORKSPACE=" + workspacePath + "\n"
            + "DATA_DIR=${DATA_DIR:-" + dataPath + "}\n"
            + "export DATA_DIR\n"
            + "PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" exec python3 -m agent.cli.main \"$@\"\n"
            + "EOF\n"
            + "chmod 755 \"$target\" 2>/dev/null || true; "
            + "done; "
            + "fi; "
            + "if ! command -v ananta >/dev/null 2>&1 && [ -x /usr/local/bin/ananta ]; then ln -sf /usr/local/bin/ananta /usr/bin/ananta 2>/dev/null || true; fi; "
            + "if ! command -v ananta >/dev/null 2>&1; then "
            + "if [ -x /usr/local/bin/ananta ]; then ANANTA_CLI=/usr/local/bin/ananta; "
            + "elif [ -x /home/ananta/.local/bin/ananta ]; then ANANTA_CLI=/home/ananta/.local/bin/ananta; "
            + "elif [ -x /root/.local/bin/ananta ]; then ANANTA_CLI=/root/.local/bin/ananta; "
            + "else echo ANANTA_CLI_MISSING; exit 5; fi; "
            + "else ANANTA_CLI=$(command -v ananta); fi; "
            + "for worker_target in /usr/local/bin/ananta-worker /home/ananta/.local/bin/ananta-worker /root/.local/bin/ananta-worker; do "
            + "cat >\"$worker_target\" <<'EOF'\n"
            + "#!/bin/sh\n"
            + "ANANTA_WORKSPACE=" + workspacePath + "\n"
            + "export ROLE=${ROLE:-worker}\n"
            + "export AGENT_NAME=${AGENT_NAME:-android-worker}\n"
            + "export PORT=${PORT:-5001}\n"
            + "export HUB_URL=${HUB_URL:-http://127.0.0.1:5000}\n"
            + "export AGENT_URL=${AGENT_URL:-http://127.0.0.1:${PORT}}\n"
            + "export DATA_DIR=${DATA_DIR:-" + dataPath + "}\n"
            + "PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" exec python3 -m agent.ai_agent \"$@\"\n"
            + "EOF\n"
            + "chmod 755 \"$worker_target\" 2>/dev/null || true; "
            + "done; "
            + "for tui_target in /usr/local/bin/ananta-tui /home/ananta/.local/bin/ananta-tui /root/.local/bin/ananta-tui; do "
            + "cat >\"$tui_target\" <<'EOF'\n"
            + "#!/bin/sh\n"
            + "exec ananta tui \"$@\"\n"
            + "EOF\n"
            + "chmod 755 \"$tui_target\" 2>/dev/null || true; "
            + "done; "
            + "\"$ANANTA_CLI\" --help >/dev/null 2>&1; "
            + "\"$ANANTA_CLI\" tui --help >/dev/null 2>&1; "
            + "if ! command -v ananta-worker >/dev/null 2>&1 && [ ! -x /usr/local/bin/ananta-worker ] && [ ! -x /home/ananta/.local/bin/ananta-worker ] && [ ! -x /root/.local/bin/ananta-worker ]; then echo ANANTA_WORKER_CMD_MISSING; exit 6; fi; "
            + "DATA_DIR=\"$ANANTA_DATA_DIR\" PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" python3 -c \"from agent.ai_agent import create_app; print('ANANTA_WORKER_DEPS_OK')\"";
    }

    @PluginMethod
    public void installOpencode(PluginCall call) {
        worker.execute(() -> {
            try {
                notifyProotProgress("opencode", "preparing", "opencode wird installiert.", -1, -1, "ubuntu");
                File runtimeRoot = runtimeRootDir();
                File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
                if (ubuntuRootfs == null) {
                    throw new IOException("Ubuntu rootfs fehlt. Bitte zuerst Distro installieren.");
                }
                String url = String.valueOf(call.getString("opencodeUrl", OPENCODE_URL)).trim();
                if (url.isEmpty()) url = OPENCODE_URL;
                String cmd = ""
                    + "set -e; "
                    + "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; rm -f /etc/apt/apt.conf.d/99proxy 2>/dev/null || true; "
                    + "if [ ! -s /etc/resolv.conf ] || ! grep -Eq '^nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then "
                    + "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf 2>/dev/null || true; "
                    + "fi; "
                    + "export DEBIAN_FRONTEND=noninteractive; "
                    + "if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then "
                    + "if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar; "
                    + "else echo ANANTA_APT_MISSING; exit 2; fi; "
                    + "fi; "
                    + "TMP_TGZ=/tmp/opencode-linux-arm64.tar.gz; TMP_DIR=/tmp/opencode-install; rm -rf \"$TMP_DIR\"; mkdir -p \"$TMP_DIR\"; "
                    + "curl -L --fail --connect-timeout 20 --max-time 600 " + shQuote(url) + " -o \"$TMP_TGZ\"; "
                    + "tar xzf \"$TMP_TGZ\" -C \"$TMP_DIR\"; "
                    + "if [ -f \"$TMP_DIR/opencode\" ]; then install -m 0755 \"$TMP_DIR/opencode\" /usr/local/bin/opencode; "
                    + "elif [ -f \"$TMP_DIR/bin/opencode\" ]; then install -m 0755 \"$TMP_DIR/bin/opencode\" /usr/local/bin/opencode; "
                    + "else CAND=$(find \"$TMP_DIR\" -type f -name opencode | head -n 1); [ -n \"$CAND\" ] && install -m 0755 \"$CAND\" /usr/local/bin/opencode || { echo ANANTA_OPENCODE_BIN_MISSING; exit 3; }; fi; "
                    + "opencode --version; echo ANANTA_OPENCODE_OK";
                ShellExecutionResult install = runInProot(runtimeRoot, ubuntuRootfs, cmd, 1200);
                String output = String.valueOf(install.output == null ? "" : install.output);
                if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_OPENCODE_OK")) {
                    throw new IOException("opencode install failed: " + output.trim());
                }
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("version", OPENCODE_VERSION);
                result.put("output", output);
                notifyProotProgress("opencode", "done", "opencode installiert.", -1, -1, "ubuntu");
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("opencode", "error", error.getMessage(), -1, -1, "ubuntu");
                call.reject("opencode installation failed: " + error.getMessage());
            }
        });
    }

    @Override
    public void load() {
        super.load();
        Log.i("AnantaProxy", "PythonRuntimePlugin loaded, starting HTTP proxy on port " + PROXY_PORT);
        ensureProxyRunning();
        Log.i("AnantaProxy", "Proxy running: " + httpProxy.isRunning());
    }

    @Override
    protected void handleOnDestroy() {
        httpProxy.stop();
        for (ShellSession session : shellSessions.values()) {
            session.close();
        }
        shellSessions.clear();
        worker.shutdownNow();
        super.handleOnDestroy();
    }
}
