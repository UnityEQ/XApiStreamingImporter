package org.gephi.plugins.xapi.core;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import org.gephi.plugins.xapi.graph.GraphWriter;
import org.gephi.plugins.xapi.graph.NetworkLogic;
import org.gephi.plugins.xapi.model.SearchResponse;
import org.gephi.plugins.xapi.transport.HttpResult;
import org.gephi.plugins.xapi.transport.XApiTransport;

/**
 * Polls GET /2/tweets/search/recent on a user-set interval. Uses since_id so each
 * poll only returns tweets newer than the previous poll.
 */
final class PollingTask implements Runnable {

    private static final String EXPANSIONS =
            "author_id,in_reply_to_user_id,"
            + "referenced_tweets.id,referenced_tweets.id.author_id,"
            + "entities.mentions.username";
    private static final String TWEET_FIELDS = "author_id,created_at,entities,referenced_tweets";
    private static final String USER_FIELDS = "username,name,profile_image_url";
    private static final int MAX_BACKOFF_MS = 15 * 60_000;

    private final SessionConfig cfg;
    private final XApiTransport transport;
    private final GraphWriter writer;
    private final NetworkLogic logic;
    private final StatusListener listener;
    private final AtomicBoolean cancelled;
    private final ObjectMapper mapper;

    private String sinceId;
    private int currentIntervalMs;

    PollingTask(SessionConfig cfg,
                XApiTransport transport,
                GraphWriter writer,
                NetworkLogic logic,
                StatusListener listener,
                AtomicBoolean cancelled) {
        this.cfg = cfg;
        this.transport = transport;
        this.writer = writer;
        this.logic = logic;
        this.listener = listener;
        this.cancelled = cancelled;
        this.mapper = new ObjectMapper()
                .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
        this.currentIntervalMs = Math.max(15, cfg.pollIntervalSeconds) * 1000;
    }

    private boolean hadError;

    @Override
    public void run() {
        try {
            while (!cancelled.get()) {
                HttpResult result;
                try {
                    result = transport.get(buildPath());
                } catch (IOException ioe) {
                    listener.onStatus(StatusListener.Level.WARN,
                            "Transport error: " + ioe.getMessage() + " (retrying)");
                    sleepInterval(Math.min(currentIntervalMs, 60_000));
                    continue;
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return;
                }

                if (result.statusCode == 401) {
                    hadError = true;
                    listener.onStatus(StatusListener.Level.ERROR,
                            "Auth failed (HTTP 401). Check that your bearer token is valid and not expired.");
                    return;
                }
                if (result.statusCode == 403) {
                    hadError = true;
                    listener.onStatus(StatusListener.Level.ERROR,
                            "Forbidden (HTTP 403). Your app does not have access to this endpoint. "
                            + "The X API Free tier is write-only; <b>search/recent requires the Basic tier "
                            + "(~$200/mo)</b> or higher. Upgrade at "
                            + "<a href='https://developer.x.com/en/portal/products'>"
                            + "developer.x.com/en/portal/products</a>.");
                    return;
                }
                if (result.statusCode == 402) {
                    hadError = true;
                    listener.onStatus(StatusListener.Level.ERROR,
                            "Payment required (HTTP 402: CreditsDepleted). Your X developer account "
                            + "has no credits for this endpoint. The Free tier does not grant read access "
                            + "to search. Upgrade to the <b>Basic tier (~$200/mo)</b> at "
                            + "<a href='https://developer.x.com/en/portal/products'>"
                            + "developer.x.com/en/portal/products</a>.");
                    return;
                }
                if (result.statusCode == 429) {
                    currentIntervalMs = Math.min(currentIntervalMs * 2, MAX_BACKOFF_MS);
                    listener.onStatus(StatusListener.Level.WARN,
                            "Rate-limited. Backing off to " + (currentIntervalMs / 1000) + "s.");
                    sleepInterval(currentIntervalMs);
                    continue;
                }
                if (!result.ok()) {
                    listener.onStatus(StatusListener.Level.WARN,
                            "HTTP " + result.statusCode + ": " + truncate(result.body, 200));
                    sleepInterval(currentIntervalMs);
                    continue;
                }

                // Reset backoff on success.
                currentIntervalMs = Math.max(15, cfg.pollIntervalSeconds) * 1000;

                SearchResponse resp;
                try {
                    resp = mapper.readValue(result.body, SearchResponse.class);
                } catch (IOException parseEx) {
                    listener.onStatus(StatusListener.Level.WARN,
                            "Bad JSON: " + parseEx.getMessage());
                    sleepInterval(currentIntervalMs);
                    continue;
                }

                int count = resp.safeData().size();
                if (count > 0) {
                    writer.processBatch(resp.safeData(), resp.includes, logic);
                    if (resp.meta != null && resp.meta.newestId != null) {
                        sinceId = resp.meta.newestId;
                    }
                }
                listener.onStatus(StatusListener.Level.RUNNING,
                        "Polled " + count + " tweets. Next in " + (currentIntervalMs / 1000) + "s.");

                sleepInterval(currentIntervalMs);
            }
        } finally {
            try { transport.close(); } catch (Exception ignored) { }
            if (!hadError) {
                listener.onStatus(StatusListener.Level.STOPPED, "Polling stopped.");
            }
        }
    }

    private String buildPath() {
        String q = URLEncoder.encode(cfg.keyword, StandardCharsets.UTF_8);
        StringBuilder sb = new StringBuilder();
        sb.append("/2/tweets/search/recent?query=").append(q);
        sb.append("&max_results=100");
        sb.append("&expansions=").append(URLEncoder.encode(EXPANSIONS, StandardCharsets.UTF_8));
        sb.append("&tweet.fields=").append(URLEncoder.encode(TWEET_FIELDS, StandardCharsets.UTF_8));
        sb.append("&user.fields=").append(URLEncoder.encode(USER_FIELDS, StandardCharsets.UTF_8));
        if (sinceId != null) {
            sb.append("&since_id=").append(sinceId);
        }
        return sb.toString();
    }

    private void sleepInterval(int ms) {
        long end = System.currentTimeMillis() + ms;
        while (!cancelled.get()) {
            long remaining = end - System.currentTimeMillis();
            if (remaining <= 0) return;
            try {
                Thread.sleep(Math.min(remaining, 500));
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    private static String truncate(String s, int max) {
        if (s == null) return "";
        return s.length() <= max ? s : s.substring(0, max) + "...";
    }
}
