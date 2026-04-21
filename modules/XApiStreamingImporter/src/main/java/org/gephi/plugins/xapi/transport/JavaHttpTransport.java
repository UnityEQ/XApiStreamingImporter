package org.gephi.plugins.xapi.transport;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

public final class JavaHttpTransport implements XApiTransport {

    private static final String BASE_URL = "https://api.x.com";

    private final HttpClient client;
    private final String bearerToken;
    private volatile HttpResponse<InputStream> inFlightStream;

    public JavaHttpTransport(String bearerToken) {
        this.bearerToken = bearerToken;
        this.client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    @Override
    public HttpResult get(String path) throws IOException, InterruptedException {
        HttpRequest req = baseRequest(path).GET().build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        return new HttpResult(resp.statusCode(), resp.body());
    }

    @Override
    public HttpResult post(String path, String jsonBody) throws IOException, InterruptedException {
        HttpRequest req = baseRequest(path)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        return new HttpResult(resp.statusCode(), resp.body());
    }

    @Override
    public void stream(String path, Consumer<String> lineConsumer, BooleanSupplier cancelled)
            throws IOException, InterruptedException {
        HttpRequest req = baseRequest(path).GET().timeout(Duration.ofMinutes(10)).build();
        HttpResponse<InputStream> resp = client.send(req, HttpResponse.BodyHandlers.ofInputStream());
        inFlightStream = resp;
        try (InputStream in = resp.body();
             BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            if (resp.statusCode() != 200) {
                throw new IOException("Stream connect failed: HTTP " + resp.statusCode());
            }
            String line;
            while (!cancelled.getAsBoolean() && (line = reader.readLine()) != null) {
                if (!line.isEmpty()) {
                    lineConsumer.accept(line);
                }
            }
        } finally {
            inFlightStream = null;
        }
    }

    @Override
    public void close() {
        HttpResponse<InputStream> s = inFlightStream;
        if (s != null) {
            try {
                s.body().close();
            } catch (IOException ignored) {
            }
        }
    }

    private HttpRequest.Builder baseRequest(String path) {
        return HttpRequest.newBuilder()
                .uri(URI.create(BASE_URL + path))
                .header("Authorization", "Bearer " + bearerToken)
                .header("User-Agent", "gephi-xapi-importer/1.0.0");
    }
}
