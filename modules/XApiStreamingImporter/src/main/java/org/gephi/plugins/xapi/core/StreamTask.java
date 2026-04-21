package org.gephi.plugins.xapi.core;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import org.gephi.plugins.xapi.graph.GraphWriter;
import org.gephi.plugins.xapi.graph.NetworkLogic;
import org.gephi.plugins.xapi.model.StreamResponse;
import org.gephi.plugins.xapi.transport.HttpResult;
import org.gephi.plugins.xapi.transport.XApiTransport;

/**
 * Maintains a single stream rule equal to the configured keyword, then reads
 * the filtered stream, dispatching each JSON line to the graph writer.
 * Requires X API Basic or higher tier.
 */
final class StreamTask implements Runnable {

    private static final String EXPANSIONS =
            "author_id,in_reply_to_user_id,"
            + "referenced_tweets.id,referenced_tweets.id.author_id,"
            + "entities.mentions.username";
    private static final String TWEET_FIELDS = "author_id,created_at,entities,referenced_tweets";
    private static final String USER_FIELDS = "username,name,profile_image_url";
    private static final String RULE_TAG = "gephi-xapi-importer";

    private final SessionConfig cfg;
    private final XApiTransport transport;
    private final GraphWriter writer;
    private final NetworkLogic logic;
    private final StatusListener listener;
    private final AtomicBoolean cancelled;
    private final ObjectMapper mapper;

    StreamTask(SessionConfig cfg,
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
    }

    private boolean hadError;

    @Override
    public void run() {
        try {
            if (!syncRules()) {
                hadError = true;
                return;
            }
            listener.onStatus(StatusListener.Level.RUNNING,
                    "Rule set. Connecting to filtered stream...");
            transport.stream(buildStreamPath(), this::onLine, cancelled::get);
        } catch (IOException e) {
            hadError = true;
            listener.onStatus(StatusListener.Level.ERROR, "Stream error: " + e.getMessage());
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        } finally {
            try { transport.close(); } catch (Exception ignored) { }
            if (!hadError) {
                listener.onStatus(StatusListener.Level.STOPPED, "Stream closed.");
            }
        }
    }

    /**
     * Ensure the remote rule list contains exactly our desired rule. Returns false on error.
     */
    private boolean syncRules() throws IOException, InterruptedException {
        HttpResult getRules = transport.get("/2/tweets/search/stream/rules");
        if (!getRules.ok()) {
            if (getRules.statusCode == 402) {
                listener.onStatus(StatusListener.Level.ERROR,
                        "Filtered stream unavailable (HTTP 402: CreditsDepleted). "
                        + "The X API uses pay-per-use credit pricing and your account has no credits. "
                        + "Purchase credits in the Developer Console at "
                        + "<a href='https://console.x.com'>console.x.com</a>, then retry.");
            } else if (getRules.statusCode == 403) {
                listener.onStatus(StatusListener.Level.ERROR,
                        "Filtered stream unavailable (HTTP 403). Your app does not have access. "
                        + "Check permissions at "
                        + "<a href='https://developer.x.com/en/portal/dashboard'>"
                        + "developer.x.com/en/portal/dashboard</a>.");
            } else {
                listener.onStatus(StatusListener.Level.ERROR,
                        "Cannot fetch stream rules (HTTP " + getRules.statusCode + "). Body: "
                        + truncate(getRules.body, 200));
            }
            return false;
        }

        List<String> staleIds = new ArrayList<>();
        boolean desiredPresent = false;
        try {
            JsonNode root = mapper.readTree(getRules.body);
            JsonNode data = root.path("data");
            if (data.isArray()) {
                for (JsonNode rule : data) {
                    String id = rule.path("id").asText(null);
                    String value = rule.path("value").asText("");
                    String tag = rule.path("tag").asText("");
                    if (RULE_TAG.equals(tag) && cfg.keyword.equals(value)) {
                        desiredPresent = true;
                    } else if (RULE_TAG.equals(tag) && id != null) {
                        staleIds.add(id);
                    }
                }
            }
        } catch (IOException e) {
            listener.onStatus(StatusListener.Level.WARN, "Could not parse rules list; continuing.");
        }

        if (!staleIds.isEmpty()) {
            StringBuilder body = new StringBuilder("{\"delete\":{\"ids\":[");
            for (int i = 0; i < staleIds.size(); i++) {
                if (i > 0) body.append(',');
                body.append('"').append(staleIds.get(i)).append('"');
            }
            body.append("]}}");
            HttpResult del = transport.post("/2/tweets/search/stream/rules", body.toString());
            if (!del.ok()) {
                listener.onStatus(StatusListener.Level.WARN,
                        "Delete-rules failed (HTTP " + del.statusCode + ")");
            }
        }

        if (!desiredPresent) {
            String body = "{\"add\":[{\"value\":" + jsonString(cfg.keyword)
                    + ",\"tag\":" + jsonString(RULE_TAG) + "}]}";
            HttpResult add = transport.post("/2/tweets/search/stream/rules", body);
            if (!add.ok()) {
                listener.onStatus(StatusListener.Level.ERROR,
                        "Add-rule failed (HTTP " + add.statusCode + "): "
                                + truncate(add.body, 200));
                return false;
            }
        }
        return true;
    }

    private void onLine(String line) {
        try {
            StreamResponse msg = mapper.readValue(line, StreamResponse.class);
            writer.processOne(msg, logic);
        } catch (IOException parseEx) {
            // Heartbeat lines ("") and keep-alive aren't tweets; ignore malformed.
        }
    }

    private String buildStreamPath() {
        StringBuilder sb = new StringBuilder("/2/tweets/search/stream?");
        sb.append("expansions=").append(URLEncoder.encode(EXPANSIONS, StandardCharsets.UTF_8));
        sb.append("&tweet.fields=").append(URLEncoder.encode(TWEET_FIELDS, StandardCharsets.UTF_8));
        sb.append("&user.fields=").append(URLEncoder.encode(USER_FIELDS, StandardCharsets.UTF_8));
        return sb.toString();
    }

    private static String jsonString(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':  sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.append('"').toString();
    }

    private static String truncate(String s, int max) {
        if (s == null) return "";
        return s.length() <= max ? s : s.substring(0, max) + "...";
    }
}
