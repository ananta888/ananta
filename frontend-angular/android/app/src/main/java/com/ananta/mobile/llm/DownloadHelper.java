package com.ananta.mobile.llm;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * Shared download and verification utilities.
 * Provides atomic downloads (write to .part, verify, rename)
 * and SHA-256 checksum computation.
 */
public final class DownloadHelper {

    /** Callback for reporting download progress. */
    public interface ProgressListener {
        void onProgress(String stage, String message, long downloadedBytes, long totalBytes);
    }

    private DownloadHelper() {}

    /**
     * Downloads a file from the given URL with atomic semantics.
     * Writes to {@code targetFile.part}, then renames to {@code targetFile} on success.
     */
    public static void downloadAtomically(String rawUrl, File targetFile, ProgressListener listener) throws IOException {
        File partFile = new File(targetFile.getParentFile(), targetFile.getName() + ".part");
        HttpURLConnection connection = null;
        try {
            URL url = new URL(rawUrl);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(300_000);
            connection.setInstanceFollowRedirects(true);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("Download failed with HTTP " + status + " from " + rawUrl);
            }
            long totalBytes = connection.getContentLengthLong();
            if (listener != null) {
                listener.onProgress("downloading", "Download gestartet.", 0, totalBytes);
            }
            try (BufferedInputStream input = new BufferedInputStream(connection.getInputStream());
                 FileOutputStream output = new FileOutputStream(partFile, false)) {
                byte[] buffer = new byte[8192];
                int read;
                long downloaded = 0L;
                long nextReport = 256 * 1024;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    downloaded += read;
                    if (downloaded >= nextReport && listener != null) {
                        listener.onProgress("downloading", "Download laeuft...", downloaded, totalBytes);
                        nextReport = downloaded + (256 * 1024);
                    }
                }
                output.flush();
                if (listener != null) {
                    listener.onProgress("downloading", "Download abgeschlossen.", downloaded, totalBytes);
                }
            }
            if (!partFile.exists() || partFile.length() == 0) {
                throw new IOException("Downloaded file is empty: " + partFile.getAbsolutePath());
            }
            if (targetFile.exists() && !targetFile.delete()) {
                throw new IOException("Could not remove existing file: " + targetFile.getAbsolutePath());
            }
            if (!partFile.renameTo(targetFile)) {
                throw new IOException("Could not rename .part to final file: " + targetFile.getAbsolutePath());
            }
        } finally {
            if (connection != null) connection.disconnect();
            if (partFile.exists()) partFile.delete();
        }
    }

    /** Computes SHA-256 hex digest of the given file. */
    public static String computeSha256(File file) throws Exception {
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

    /**
     * Verifies that a file's SHA-256 matches the expected value.
     * @throws IOException if the hash does not match
     */
    public static void verifySha256(File file, String expectedSha256) throws Exception {
        String actual = computeSha256(file);
        if (!actual.equalsIgnoreCase(expectedSha256)) {
            throw new IOException("SHA256 mismatch for " + file.getName()
                + ": expected " + expectedSha256 + ", got " + actual);
        }
    }

    /** Creates the directory (and parents) if it doesn't exist. */
    public static File ensureDirectory(File dir) throws IOException {
        if (dir.exists()) {
            if (dir.isDirectory()) return dir;
            throw new IOException("Path exists but is not a directory: " + dir.getAbsolutePath());
        }
        if (dir.mkdirs()) return dir;
        throw new IOException("Could not create directory: " + dir.getAbsolutePath());
    }
}
