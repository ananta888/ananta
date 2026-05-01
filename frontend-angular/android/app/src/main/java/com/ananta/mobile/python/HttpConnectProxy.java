package com.ananta.mobile.python;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import android.util.Log;

/**
 * Lightweight HTTP CONNECT proxy for proot networking.
 *
 * Android blocks external TCP from proot-traced processes, but localhost
 * connections work. This proxy runs inside the app's own process (which has
 * INTERNET permission) and forwards CONNECT tunnels on behalf of proot
 * clients that set {@code http_proxy / https_proxy} env vars.
 *
 * Also supports plain HTTP forwarding for tools like apt that send full
 * URL requests through the proxy.
 */
final class HttpConnectProxy {

    private static final int RELAY_BUFFER = 8192;
    private static final int CONNECT_TIMEOUT_MS = 10_000;
    private static final int SO_TIMEOUT_MS = 60_000;

    private final int port;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicInteger activeConnections = new AtomicInteger(0);
    private ServerSocket serverSocket;
    private ExecutorService pool;
    private Thread acceptThread;

    HttpConnectProxy(int port) {
        this.port = port;
    }

    synchronized void start() throws IOException {
        if (running.get()) return;
        serverSocket = new ServerSocket();
        serverSocket.setReuseAddress(true);
        serverSocket.bind(new InetSocketAddress("127.0.0.1", port));
        pool = Executors.newCachedThreadPool(r -> {
            Thread t = new Thread(r, "proxy-relay");
            t.setDaemon(true);
            return t;
        });
        running.set(true);
        acceptThread = new Thread(this::acceptLoop, "proxy-accept");
        acceptThread.setDaemon(true);
        acceptThread.start();
    }

    synchronized void stop() {
        if (!running.compareAndSet(true, false)) return;
        try { serverSocket.close(); } catch (IOException ignored) {}
        if (pool != null) pool.shutdownNow();
        pool = null;
    }

    boolean isRunning() { return running.get(); }
    int getPort() { return port; }
    int getActiveConnections() { return activeConnections.get(); }

    private void acceptLoop() {
        while (running.get()) {
            try {
                Socket client = serverSocket.accept();
                client.setSoTimeout(SO_TIMEOUT_MS);
                pool.submit(() -> handleClient(client));
            } catch (IOException e) {
                if (running.get()) {
                    // unexpected error, keep accepting
                }
            }
        }
    }

    private void handleClient(Socket client) {
        activeConnections.incrementAndGet();
        try {
            InputStream in = client.getInputStream();
            // Read the request line
            String requestLine = readLine(in);
            if (requestLine == null || requestLine.isEmpty()) return;

            if (requestLine.startsWith("CONNECT ")) {
                handleConnect(client, in, requestLine);
            } else {
                handlePlainHttp(client, in, requestLine);
            }
        } catch (IOException e) {
            Log.w("AnantaProxy", "Client error: " + e.getMessage());
        } finally {
            activeConnections.decrementAndGet();
            closeQuietly(client);
        }
    }

    /**
     * CONNECT tunnel — used by HTTPS clients (pip, git, curl).
     * Format: CONNECT host:port HTTP/1.1
     */
    private void handleConnect(Socket client, InputStream clientIn, String requestLine) throws IOException {
        // Parse host:port from "CONNECT host:port HTTP/1.x"
        String[] parts = requestLine.split("\\s+");
        if (parts.length < 2) return;
        String target = parts[1];
        String host;
        int targetPort;
        int colon = target.lastIndexOf(':');
        if (colon > 0) {
            host = target.substring(0, colon);
            targetPort = Integer.parseInt(target.substring(colon + 1));
        } else {
            host = target;
            targetPort = 443;
        }

        // Consume remaining headers until blank line
        while (true) {
            String header = readLine(clientIn);
            if (header == null || header.isEmpty()) break;
        }

        // Connect to remote host (prefer IPv4)
        Socket remote;
        try {
            remote = connectToHost(host, targetPort);
            Log.d("AnantaProxy", "CONNECT tunnel to " + host + ":" + targetPort + " established");
        } catch (IOException e) {
            Log.w("AnantaProxy", "CONNECT failed to " + host + ":" + targetPort + ": " + e.getMessage());
            String resp = "HTTP/1.1 502 Bad Gateway\r\n\r\n";
            client.getOutputStream().write(resp.getBytes(StandardCharsets.US_ASCII));
            return;
        }

        // Send 200 OK to client
        String ok = "HTTP/1.1 200 Connection Established\r\n\r\n";
        client.getOutputStream().write(ok.getBytes(StandardCharsets.US_ASCII));
        client.getOutputStream().flush();

        // Relay data bidirectionally
        relay(client, remote);
    }

    /**
     * Plain HTTP proxy — used by apt (sends full URL through proxy).
     * Format: GET http://host/path HTTP/1.1
     */
    private void handlePlainHttp(Socket client, InputStream clientIn, String requestLine) throws IOException {
        // Parse host from URL in request line
        String[] parts = requestLine.split("\\s+");
        if (parts.length < 3) return;
        String method = parts[0];
        String url = parts[1];
        String httpVersion = parts[2];

        // Extract host and path from absolute URL
        String host;
        int targetPort = 80;
        String path;
        if (url.startsWith("http://")) {
            String rest = url.substring(7);
            int slashIdx = rest.indexOf('/');
            String hostPort = slashIdx >= 0 ? rest.substring(0, slashIdx) : rest;
            path = slashIdx >= 0 ? rest.substring(slashIdx) : "/";
            int colon = hostPort.lastIndexOf(':');
            if (colon > 0) {
                host = hostPort.substring(0, colon);
                targetPort = Integer.parseInt(hostPort.substring(colon + 1));
            } else {
                host = hostPort;
            }
        } else {
            // Not an absolute URL, can't proxy
            String resp = "HTTP/1.1 400 Bad Request\r\n\r\n";
            client.getOutputStream().write(resp.getBytes(StandardCharsets.US_ASCII));
            return;
        }

        // Read all headers
        StringBuilder headers = new StringBuilder();
        int contentLength = 0;
        while (true) {
            String header = readLine(clientIn);
            if (header == null || header.isEmpty()) break;
            if (header.toLowerCase().startsWith("content-length:")) {
                contentLength = Integer.parseInt(header.substring(15).trim());
            }
            // Skip proxy-specific headers
            if (header.toLowerCase().startsWith("proxy-")) continue;
            headers.append(header).append("\r\n");
        }

        // Connect to remote (prefer IPv4)
        Socket remote;
        try {
            remote = connectToHost(host, targetPort);
            Log.d("AnantaProxy", "HTTP proxy to " + host + ":" + targetPort + " path=" + path);
        } catch (IOException e) {
            Log.w("AnantaProxy", "HTTP proxy connect failed to " + host + ":" + targetPort + ": " + e.getMessage());
            String resp = "HTTP/1.1 502 Bad Gateway\r\n\r\n";
            client.getOutputStream().write(resp.getBytes(StandardCharsets.US_ASCII));
            return;
        }

        // Forward request with relative path
        OutputStream remoteOut = remote.getOutputStream();
        String newRequestLine = method + " " + path + " " + httpVersion + "\r\n";
        remoteOut.write(newRequestLine.getBytes(StandardCharsets.US_ASCII));
        remoteOut.write(headers.toString().getBytes(StandardCharsets.US_ASCII));
        remoteOut.write("\r\n".getBytes(StandardCharsets.US_ASCII));

        // Forward body if present
        if (contentLength > 0) {
            byte[] buf = new byte[Math.min(contentLength, RELAY_BUFFER)];
            int remaining = contentLength;
            while (remaining > 0) {
                int n = clientIn.read(buf, 0, Math.min(buf.length, remaining));
                if (n <= 0) break;
                remoteOut.write(buf, 0, n);
                remaining -= n;
            }
        }
        remoteOut.flush();

        // Relay response back
        InputStream remoteIn = remote.getInputStream();
        OutputStream clientOut = client.getOutputStream();
        byte[] buf = new byte[RELAY_BUFFER];
        int n;
        while ((n = remoteIn.read(buf)) != -1) {
            clientOut.write(buf, 0, n);
            clientOut.flush();
        }

        closeQuietly(remote);
    }

    private void relay(Socket a, Socket b) {
        Thread ab = new Thread(() -> pipe(a, b), "relay-a2b");
        ab.setDaemon(true);
        ab.start();
        pipe(b, a);
        closeQuietly(a);
        closeQuietly(b);
    }

    /** Resolve hostname preferring IPv4 to avoid IPv6 connectivity issues on mobile. */
    private Socket connectToHost(String host, int port) throws IOException {
        InetAddress[] addresses = InetAddress.getAllByName(host);
        // Prefer IPv4
        InetAddress target = null;
        for (InetAddress addr : addresses) {
            if (addr instanceof Inet4Address) {
                target = addr;
                break;
            }
        }
        if (target == null && addresses.length > 0) {
            target = addresses[0];
        }
        if (target == null) {
            throw new IOException("Cannot resolve host: " + host);
        }
        Socket socket = new Socket();
        socket.connect(new InetSocketAddress(target, port), CONNECT_TIMEOUT_MS);
        socket.setSoTimeout(SO_TIMEOUT_MS);
        return socket;
    }

    private void pipe(Socket from, Socket to) {
        byte[] buf = new byte[RELAY_BUFFER];
        try {
            InputStream in = from.getInputStream();
            OutputStream out = to.getOutputStream();
            int n;
            while ((n = in.read(buf)) != -1) {
                out.write(buf, 0, n);
                out.flush();
            }
        } catch (IOException ignored) {
        }
    }

    private String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder(256);
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') sb.append((char) c);
        }
        return c == -1 && sb.length() == 0 ? null : sb.toString();
    }

    private void closeQuietly(Socket s) {
        try { s.close(); } catch (IOException ignored) {}
    }
}
