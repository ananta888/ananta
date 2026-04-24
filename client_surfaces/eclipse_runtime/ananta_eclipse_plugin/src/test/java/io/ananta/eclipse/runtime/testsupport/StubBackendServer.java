package io.ananta.eclipse.runtime.testsupport;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

public final class StubBackendServer implements AutoCloseable {
    private final HttpServer server;
    private final Map<String, StubResponse> responses = new ConcurrentHashMap<>();
    private final List<CapturedRequest> capturedRequests = Collections.synchronizedList(new ArrayList<>());

    private StubBackendServer(HttpServer server) {
        this.server = server;
        this.server.createContext("/", this::handle);
        this.server.setExecutor(null);
        this.server.start();
    }

    public static StubBackendServer start() throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        return new StubBackendServer(server);
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stub(String method, String path, int statusCode, String body) {
        String key = key(method, path);
        responses.put(key, new StubResponse(statusCode, Objects.toString(body, "")));
    }

    public List<CapturedRequest> capturedRequests() {
        synchronized (capturedRequests) {
            return List.copyOf(capturedRequests);
        }
    }

    public List<CapturedRequest> findRequests(String method, String path) {
        String normalizedMethod = Objects.toString(method, "").trim().toUpperCase();
        String normalizedPath = normalizePath(path);
        synchronized (capturedRequests) {
            return capturedRequests.stream()
                    .filter(request -> request.method().equals(normalizedMethod) && request.path().equals(normalizedPath))
                    .toList();
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = Objects.toString(exchange.getRequestMethod(), "").trim().toUpperCase();
        String path = normalizePath(exchange.getRequestURI().getPath());
        String query = Objects.toString(exchange.getRequestURI().getRawQuery(), "").trim();
        String pathWithQuery = query.isEmpty() ? path : path + "?" + query;
        String requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        capturedRequests.add(new CapturedRequest(method, path, query, requestBody));

        StubResponse response = responses.get(key(method, pathWithQuery));
        if (response == null) {
            response = responses.get(key(method, path));
        }
        if (response == null) {
            response = new StubResponse(404, "{\"error\":\"stub_not_found\"}");
        }

        byte[] encoded = response.body().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.statusCode(), encoded.length);
        exchange.getResponseBody().write(encoded);
        exchange.close();
    }

    private static String key(String method, String path) {
        return Objects.toString(method, "").trim().toUpperCase() + " " + normalizePath(path);
    }

    private static String normalizePath(String value) {
        String normalized = Objects.toString(value, "").trim();
        if (normalized.isEmpty()) {
            return "/";
        }
        if (!normalized.startsWith("/")) {
            return "/" + normalized;
        }
        return normalized;
    }

    @Override
    public void close() {
        server.stop(0);
    }

    public record StubResponse(
            int statusCode,
            String body
    ) {
    }

    public record CapturedRequest(
            String method,
            String path,
            String query,
            String body
    ) {
    }
}
