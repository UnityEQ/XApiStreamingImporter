package org.gephi.plugins.xapi.transport;

import java.io.IOException;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * A thin abstraction over "make an X API v2 request". Two implementations:
 * {@link JavaHttpTransport} (bearer token + java.net.http) and
 * {@link XurlTransport} (subprocess to the xurl CLI).
 */
public interface XApiTransport extends AutoCloseable {

    /**
     * @param path X API path starting with "/", e.g. "/2/tweets/search/recent?query=nasa".
     */
    HttpResult get(String path) throws IOException, InterruptedException;

    HttpResult post(String path, String jsonBody) throws IOException, InterruptedException;

    /**
     * Open a long-lived stream. Blocks until the server ends the stream,
     * the transport is closed, or {@code cancelled.getAsBoolean()} returns true.
     * Each non-empty JSON line is delivered to {@code lineConsumer}.
     */
    void stream(String path, Consumer<String> lineConsumer, BooleanSupplier cancelled)
            throws IOException, InterruptedException;

    @Override
    void close();
}
