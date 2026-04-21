package org.gephi.plugins.xapi.transport;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * Shells out to the {@code xurl} binary (https://github.com/xdevplatform/xurl).
 * xurl must be on the user's PATH and pre-authenticated (run {@code xurl auth oauth2}
 * once before using this transport).
 */
public final class XurlTransport implements XApiTransport {

    private volatile Process current;

    @Override
    public HttpResult get(String path) throws IOException, InterruptedException {
        return run(List.of("xurl", path), null);
    }

    @Override
    public HttpResult post(String path, String jsonBody) throws IOException, InterruptedException {
        return run(List.of("xurl", "-X", "POST", "-d", jsonBody, path), null);
    }

    @Override
    public void stream(String path, Consumer<String> lineConsumer, BooleanSupplier cancelled)
            throws IOException, InterruptedException {
        ProcessBuilder pb = new ProcessBuilder("xurl", path)
                .redirectErrorStream(false);
        Process p;
        try {
            p = pb.start();
        } catch (IOException e) {
            throw new IOException("Could not launch xurl; is it installed and on PATH? "
                    + "Install: https://github.com/xdevplatform/xurl", e);
        }
        current = p;
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while (!cancelled.getAsBoolean() && (line = reader.readLine()) != null) {
                if (!line.isEmpty()) {
                    lineConsumer.accept(line);
                }
            }
        } finally {
            if (p.isAlive()) {
                p.destroy();
            }
            current = null;
        }
    }

    @Override
    public void close() {
        Process p = current;
        if (p != null && p.isAlive()) {
            p.destroy();
        }
    }

    private HttpResult run(List<String> command, File cwd) throws IOException, InterruptedException {
        ProcessBuilder pb = new ProcessBuilder(command).redirectErrorStream(false);
        if (cwd != null) {
            pb.directory(cwd);
        }
        Process p;
        try {
            p = pb.start();
        } catch (IOException e) {
            throw new IOException("Could not launch xurl; install it from "
                    + "https://github.com/xdevplatform/xurl and run 'xurl auth oauth2' first.", e);
        }
        current = p;
        try {
            String stdout = readAll(p.getInputStream());
            String stderr = readAll(p.getErrorStream());
            int exit = p.waitFor();
            if (exit == 0) {
                return new HttpResult(200, stdout);
            }
            int inferredStatus = inferStatus(stdout, stderr);
            return new HttpResult(inferredStatus, stdout.isEmpty() ? stderr : stdout);
        } finally {
            current = null;
        }
    }

    private static String readAll(java.io.InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            boolean first = true;
            while ((line = r.readLine()) != null) {
                if (!first) sb.append('\n');
                sb.append(line);
                first = false;
            }
        }
        return sb.toString();
    }

    private static int inferStatus(String stdout, String stderr) {
        String combined = (stdout + " " + stderr).toLowerCase();
        if (combined.contains("401") || combined.contains("unauthorized")) return 401;
        if (combined.contains("403") || combined.contains("forbidden")) return 403;
        if (combined.contains("429") || combined.contains("rate limit")) return 429;
        return 500;
    }
}
