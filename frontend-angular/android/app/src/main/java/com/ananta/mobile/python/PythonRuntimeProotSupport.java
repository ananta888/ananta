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

abstract class PythonRuntimeProotSupport extends PythonRuntimeCore {
    protected boolean isPythonAvailable() {
        try {
            Class.forName("com.chaquo.python.Python");
            return true;
        } catch (ClassNotFoundException error) {
            return false;
        }
    }

    protected String invokePython(String functionName) throws Exception {
        if (!isPythonAvailable()) {
            throw new IllegalStateException("Embedded Python is disabled. Enable anantaEnablePythonRuntime=true.");
        }

        ensurePythonStarted();
        Class<?> pythonClass = Class.forName("com.chaquo.python.Python");
        Method getInstance = pythonClass.getMethod("getInstance");
        Object python = getInstance.invoke(null);
        Method getModule = pythonClass.getMethod("getModule", String.class);
        Object module = getModule.invoke(python, "ananta_runtime");

        Class<?> pyObjectClass = Class.forName("com.chaquo.python.PyObject");
        Method callAttr = pyObjectClass.getMethod("callAttr", String.class, Object[].class);
        Object value = callAttr.invoke(module, functionName, new Object[]{});
        return value == null ? "" : String.valueOf(value);
    }

    protected void ensurePythonStarted() throws Exception {
        Class<?> pythonClass = Class.forName("com.chaquo.python.Python");
        Method isStarted = pythonClass.getMethod("isStarted");
        boolean started = (boolean) isStarted.invoke(null);
        if (started) return;

        Context context = getContext();
        if (context == null) {
            throw new IllegalStateException("Android context unavailable for Python startup.");
        }

        Class<?> androidPlatformClass = Class.forName("com.chaquo.python.android.AndroidPlatform");
        Object androidPlatform = androidPlatformClass.getConstructor(Context.class).newInstance(context);

        Method startMethod = null;
        for (Method candidate : pythonClass.getMethods()) {
            if (!"start".equals(candidate.getName()) || candidate.getParameterCount() != 1) continue;
            Class<?> parameterType = candidate.getParameterTypes()[0];
            if (parameterType.isAssignableFrom(androidPlatformClass)) {
                startMethod = candidate;
                break;
            }
        }
        if (startMethod == null) {
            throw new NoSuchMethodException("Python.start(...) compatible with AndroidPlatform not found.");
        }
        startMethod.invoke(null, androidPlatform);
    }

    protected ShellExecutionResult executeShellCommand(String command, int timeoutSeconds) throws Exception {
        File workingDir = resolveShellWorkingDirectory("");
        ProcessBuilder builder = new ProcessBuilder("/system/bin/sh", "-lc", command);
        builder.directory(workingDir);
        applyShellEnvironment(builder, workingDir);
        Process process = builder
            .redirectErrorStream(true)
            .start();

        boolean finished = process.waitFor(timeoutSeconds, TimeUnit.SECONDS);
        if (!finished) {
            process.destroy();
            if (!process.waitFor(1, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                process.waitFor(1, TimeUnit.SECONDS);
            }
        }

        String output = readProcessOutput(process.getInputStream(), MAX_SHELL_OUTPUT_CHARS);
        if (!finished) {
            output = (output + "\n[ananta-mobile-shell] Timeout after " + timeoutSeconds + "s").trim();
            return new ShellExecutionResult(output, -1, true);
        }

        return new ShellExecutionResult(output, process.exitValue(), false);
    }

    protected File resolveShellWorkingDirectory(String requestedCwd) {
        String cwd = String.valueOf(requestedCwd == null ? "" : requestedCwd).trim();
        if (!cwd.isEmpty()) {
            File requested = new File(cwd);
            if (requested.isDirectory() && requested.canRead()) {
                return requested;
            }
        }

        Context context = getContext();
        if (context != null) {
            File filesDir = context.getFilesDir();
            if (filesDir != null && filesDir.isDirectory() && filesDir.canRead()) {
                return filesDir;
            }
        }

        File termuxHome = new File("/data/data/com.termux/files/home");
        if (termuxHome.isDirectory() && termuxHome.canRead()) {
            return termuxHome;
        }

        return new File("/");
    }

    protected void applyShellEnvironment(ProcessBuilder builder, File workingDir) {
        Map<String, String> env = builder.environment();
        // Prevent Chaquopy's Python 3.11 env from leaking into proot sessions running Python 3.13
        env.remove("PYTHONPATH");
        env.remove("PYTHONHOME");
        env.remove("PYTHONDONTWRITEBYTECODE");
        env.remove("PYTHONSTARTUP");
        String path = workingDir.getAbsolutePath();
        env.put("HOME", path);
        env.put("PWD", path);
        env.put("ANANTA_MOBILE_FILES", path);
        File runtimeRoot = runtimeRootDir();
        File prootBin = new File(runtimeRoot, "bin");
        String existingPath = String.valueOf(env.getOrDefault("PATH", ""));
        env.put("PATH", prootBin.getAbsolutePath() + (existingPath.isEmpty() ? "" : ":" + existingPath));
        env.putIfAbsent("TERM", "xterm-256color");
        env.put("PROOT_FORCE_KOMPAT", "1");
        env.put("GLIBC_TUNABLES", "glibc.pthread.rseq=0");
        File prootTmp = new File(runtimeRoot, "tmp");
        if (!prootTmp.exists()) prootTmp.mkdirs();
        env.put("PROOT_TMP_DIR", prootTmp.getAbsolutePath());
        // Resolve proot-loader from APK native libs — required for execve under untrusted_app SELinux domain
        String prootLoaderPath = resolveNativeLibPath("libproot-loader.so");
        if (prootLoaderPath != null) {
            env.put("PROOT_LOADER", prootLoaderPath);
        }
        ensureProotLoaderSymlink();
        // libtalloc.so is needed by Termux-forked proot; ensure it's findable
        String nativeLibDir = resolveNativeLibPath("libprootclassic.so");
        if (nativeLibDir != null) {
            String nativeDir = new File(nativeLibDir).getParent();
            String ldPath = env.getOrDefault("LD_LIBRARY_PATH", "");
            if (ldPath.isEmpty()) {
                env.put("LD_LIBRARY_PATH", nativeDir);
            } else if (!ldPath.contains(nativeDir)) {
                env.put("LD_LIBRARY_PATH", nativeDir + ":" + ldPath);
            }
        }
        // HTTP proxy for proot external network access
        if (httpProxy.isRunning()) {
            String proxyUrl = "http://127.0.0.1:" + PROXY_PORT;
            env.put("http_proxy", proxyUrl);
            env.put("https_proxy", proxyUrl);
            env.put("HTTP_PROXY", proxyUrl);
            env.put("HTTPS_PROXY", proxyUrl);
        }
    }

    protected File runtimeRootDir() {
        return new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR);
    }

    protected String resolveNativeLibPath(String libName) {
        Context context = getContext();
        if (context == null) return null;
        ApplicationInfo appInfo = context.getApplicationInfo();
        if (appInfo == null || appInfo.nativeLibraryDir == null) return null;
        File lib = new File(appInfo.nativeLibraryDir, libName);
        return lib.isFile() ? lib.getAbsolutePath() : null;
    }

    /**
     * Ensures a symlink at /data/data/com.ananta.mobile/ldr/<libName> pointing
     * to the actual native lib in the APK directory. Required because proot
     * has a hardcoded loader path that must resolve at runtime.
     */
    protected void ensureProotLoaderSymlink() {
        Context context = getContext();
        if (context == null) return;
        try {
            String loaderSrc = resolveNativeLibPath("libproot-loader.so");
            if (loaderSrc == null) return;
            File ldrDir = new File(context.getDataDir(), "ldr");
            if (!ldrDir.exists()) ldrDir.mkdirs();
            File symlinkFile = new File(ldrDir, "libproot-loader.so");
            Path symlinkPath = symlinkFile.toPath();
            if (Files.isSymbolicLink(symlinkPath)) {
                String existing = Files.readSymbolicLink(symlinkPath).toString();
                if (existing.equals(loaderSrc)) return;
                Files.delete(symlinkPath);
            } else if (symlinkFile.exists()) {
                symlinkFile.delete();
            }
            Files.createSymbolicLink(symlinkPath, Paths.get(loaderSrc));
        } catch (Exception e) {
            // Loader symlink creation failed — proot will fall back to embedded loader
        }
    }

    protected File ensureDir(File parent, String child) throws IOException {
        File dir = new File(parent, child);
        if (dir.exists()) {
            if (dir.isDirectory()) return dir;
            throw new IOException("Path is not a directory: " + dir.getAbsolutePath());
        }
        if (dir.mkdirs()) return dir;
        throw new IOException("Could not create directory: " + dir.getAbsolutePath());
    }

    protected void ensureProotInstalled() throws IOException {
        File binDir = new File(runtimeRootDir(), "bin");
        File wrapper = new File(binDir, PROOT_WRAPPER_FILE);
        File runtimeBinary = new File(binDir, PROOT_BIN_FILE);
        File classicBinary = new File(binDir, PROOT_CLASSIC_FILE);
        File selectedBinary = resolveProotBinaryCandidate(
            embeddedNativeClassicProotBinary(),
            embeddedNativeProotBinary(),
            classicBinary,
            runtimeBinary
        );
        if (!wrapper.exists() || !wrapper.canExecute() || selectedBinary == null || !selectedBinary.exists()) {
            throw new IOException("Proot runtime is not installed. Install runtime first.");
        }
        selectedBinary.setReadable(true, false);
        selectedBinary.setExecutable(true, false);
        createProotWrapper(binDir, selectedBinary);
    }

    protected void createProotWrapper(File binDir, File targetBinary) throws IOException {
        File wrapper = new File(binDir, PROOT_WRAPPER_FILE);
        String script = "#!/system/bin/sh\nexec \"" + targetBinary.getAbsolutePath() + "\" \"$@\"\n";
        try (FileOutputStream output = new FileOutputStream(wrapper, false)) {
            output.write(script.getBytes(StandardCharsets.UTF_8));
            output.flush();
        }
        if (!wrapper.setExecutable(true, false)) {
            throw new IOException("Could not mark proot wrapper executable.");
        }
    }

    protected File embeddedNativeProotBinary() {
        Context context = getContext();
        if (context == null || context.getApplicationInfo() == null) return null;
        String nativeDir = String.valueOf(context.getApplicationInfo().nativeLibraryDir == null ? "" : context.getApplicationInfo().nativeLibraryDir).trim();
        if (nativeDir.isEmpty()) return null;
        File candidate = new File(nativeDir, PROOT_EMBEDDED_LIB_FILE);
        return candidate.exists() ? candidate : null;
    }

    protected File embeddedNativeClassicProotBinary() {
        Context context = getContext();
        if (context == null || context.getApplicationInfo() == null) return null;
        String nativeDir = String.valueOf(context.getApplicationInfo().nativeLibraryDir == null ? "" : context.getApplicationInfo().nativeLibraryDir).trim();
        if (nativeDir.isEmpty()) return null;
        File candidate = new File(nativeDir, PROOT_CLASSIC_EMBEDDED_LIB_FILE);
        return candidate.exists() ? candidate : null;
    }

    protected File resolveProotBinaryCandidate(File embeddedClassicBinary, File embeddedBinary, File classicBinary, File runtimeBinary) {
        if (isUsableBinary(embeddedClassicBinary)) return embeddedClassicBinary;
        if (isUsableBinary(embeddedBinary)) return embeddedBinary;
        if (isUsableBinary(classicBinary)) return classicBinary;
        if (isUsableBinary(runtimeBinary)) return runtimeBinary;
        return null;
    }

    protected String resolveProotBinarySource(File selected, File embeddedClassic, File embeddedRs, File classicRuntime) {
        if (isEmbeddedBinary(selected, embeddedClassic)) return "embedded-classic-native-lib";
        if (isEmbeddedBinary(selected, embeddedRs)) return "embedded-native-lib";
        if (selected != null && classicRuntime != null) {
            try {
                if (selected.getCanonicalPath().equals(classicRuntime.getCanonicalPath())) {
                    return "classic-runtime-files";
                }
            } catch (IOException ignored) {
                if (selected.getAbsolutePath().equals(classicRuntime.getAbsolutePath())) return "classic-runtime-files";
            }
        }
        return "runtime-files";
    }

    protected boolean isUsableBinary(File file) {
        return file != null && file.exists() && file.isFile();
    }

    protected boolean isEmbeddedBinary(File selected, File embedded) {
        if (selected == null || embedded == null) return false;
        try {
            return selected.getCanonicalPath().equals(embedded.getCanonicalPath());
        } catch (IOException ignored) {
            return selected.getAbsolutePath().equals(embedded.getAbsolutePath());
        }
    }

    protected ProotProbeResult probeProotWrapper(File wrapper) {
        Process process = null;
        try {
            process = new ProcessBuilder("/system/bin/sh", wrapper.getAbsolutePath(), "--version")
                .redirectErrorStream(true)
                .start();
            boolean finished = process.waitFor(8, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                return new ProotProbeResult(false, "Runtime-Check Timeout.");
            }
            String output = readProcessOutput(process.getInputStream(), 4000);
            int exitCode = process.exitValue();
            if (exitCode == 0) {
                return new ProotProbeResult(true, output.isBlank() ? "Runtime startbar." : output);
            }
            String details = output.isBlank() ? "Exit " + exitCode : output;
            return new ProotProbeResult(false, "Runtime nicht startbar: " + details);
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage() == null ? "" : error.getMessage()).trim();
            if (message.isEmpty()) message = error.getClass().getSimpleName();
            return new ProotProbeResult(false, "Runtime-Check fehlgeschlagen: " + message);
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }

    protected void downloadToFile(String rawUrl, File targetFile, String operation) throws IOException {
        downloadToFile(rawUrl, targetFile, operation, null);
    }

    protected void downloadToFile(String rawUrl, File targetFile, String operation, String distro) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(rawUrl);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(180000);
            connection.setInstanceFollowRedirects(true);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("Download failed with HTTP " + status + " from " + rawUrl);
            }
            long totalBytes = connection.getContentLengthLong();
            notifyProotProgress(operation, "downloading", "Download gestartet.", 0, totalBytes, distro);
            try (InputStream input = new BufferedInputStream(connection.getInputStream());
                 FileOutputStream output = new FileOutputStream(targetFile, false)) {
                byte[] buffer = new byte[8192];
                int read;
                long downloaded = 0L;
                long nextReport = 256 * 1024;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    downloaded += read;
                    if (downloaded >= nextReport) {
                        notifyProotProgress(operation, "downloading", "Download laeuft...", downloaded, totalBytes, distro);
                        nextReport = downloaded + (256 * 1024);
                    }
                }
                output.flush();
                notifyProotProgress(operation, "downloading", "Download abgeschlossen.", downloaded, totalBytes, distro);
            }
            if (!targetFile.exists() || targetFile.length() == 0) {
                throw new IOException("Downloaded file is empty: " + targetFile.getAbsolutePath());
            }
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    protected void notifyProotProgress(String operation, String stage, String message, long downloaded, long total, String distro) {
        JSObject event = new JSObject();
        event.put("operation", operation);
        event.put("stage", stage);
        event.put("message", message);
        event.put("downloadedBytes", downloaded);
        event.put("totalBytes", total);
        if (total > 0 && downloaded >= 0) {
            event.put("progress", Math.min(1.0, (double) downloaded / (double) total));
        } else {
            event.put("progress", -1);
        }
        if (distro != null && !distro.isBlank()) {
            event.put("distro", distro);
        }
        notifyListeners("prootInstallProgress", event);
    }

    protected DistroDownloadMeta resolveDistroDownloadMeta(String distro) throws IOException {
        DistroDownloadMeta fromPlugin = resolveDistroFromPluginScript(distro);
        if (fromPlugin != null && fromPlugin.url != null && !fromPlugin.url.isBlank()) {
            return fromPlugin;
        }
        String fromRelease = resolveDistroAssetUrlFromRelease(distro);
        if (fromRelease == null || fromRelease.isBlank()) {
            return new DistroDownloadMeta(null, null);
        }
        return new DistroDownloadMeta(fromRelease, null);
    }

    protected DistroDownloadMeta resolveDistroFromPluginScript(String distro) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(PROOT_DISTRO_PLUGIN_BASE + distro + ".sh");
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                return null;
            }
            String body = readProcessOutput(connection.getInputStream(), 200_000);
            String urlMarker = "TARBALL_URL['aarch64']=\"";
            int urlStart = body.indexOf(urlMarker);
            if (urlStart < 0) return null;
            int urlValueStart = urlStart + urlMarker.length();
            int urlValueEnd = body.indexOf('"', urlValueStart);
            if (urlValueEnd <= urlValueStart) return null;
            String archiveUrl = body.substring(urlValueStart, urlValueEnd).trim();

            String shaMarker = "TARBALL_SHA256['aarch64']=\"";
            int shaStart = body.indexOf(shaMarker);
            String sha = null;
            if (shaStart >= 0) {
                int shaValueStart = shaStart + shaMarker.length();
                int shaValueEnd = body.indexOf('"', shaValueStart);
                if (shaValueEnd > shaValueStart) {
                    sha = body.substring(shaValueStart, shaValueEnd).trim();
                }
            }
            return new DistroDownloadMeta(archiveUrl, sha);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    protected String resolveDistroAssetUrlFromRelease(String distro) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(PROOT_DISTRO_RELEASE_API);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("Could not query distro release API. HTTP " + status);
            }
            String body = readProcessOutput(connection.getInputStream(), 200_000);
            String marker = "\"" + distro + "-aarch64-pd-";
            int markerIndex = body.indexOf(marker);
            if (markerIndex < 0) return null;
            int urlKeyIndex = body.indexOf("\"browser_download_url\":\"", markerIndex);
            if (urlKeyIndex < 0) return null;
            int start = urlKeyIndex + "\"browser_download_url\":\"".length();
            int end = body.indexOf('"', start);
            if (end <= start) return null;
            String escaped = body.substring(start, end);
            return escaped.replace("\\/", "/");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

}
