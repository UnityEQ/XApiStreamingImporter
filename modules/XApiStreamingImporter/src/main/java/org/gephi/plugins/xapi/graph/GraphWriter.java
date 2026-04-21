package org.gephi.plugins.xapi.graph;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.gephi.graph.api.Column;
import org.gephi.graph.api.DirectedGraph;
import org.gephi.graph.api.Edge;
import org.gephi.graph.api.GraphController;
import org.gephi.graph.api.GraphFactory;
import org.gephi.graph.api.GraphModel;
import org.gephi.graph.api.Node;
import org.gephi.graph.api.Origin;
import org.gephi.graph.api.Table;
import org.gephi.plugins.xapi.model.Includes;
import org.gephi.plugins.xapi.model.StreamResponse;
import org.gephi.plugins.xapi.model.Tweet;
import org.gephi.plugins.xapi.model.User;
import org.openide.util.Lookup;

/**
 * Owns graph mutation. One write lock per {@link #processBatch} call.
 * Columns are created lazily on first use. Nodes are keyed by X user id;
 * edges by composite "src:dst:type" so repeat interactions bump weight.
 */
public final class GraphWriter {

    public static final String ATTR_USERNAME = "username";
    public static final String ATTR_NAME = "name";
    public static final String ATTR_PROFILE_IMAGE = "profile_image_url";
    public static final String ATTR_INTERACTION = "interaction";

    private final GraphModel model;
    private boolean columnsReady;

    public GraphWriter() {
        GraphController gc = Lookup.getDefault().lookup(GraphController.class);
        if (gc == null) {
            throw new IllegalStateException("Gephi GraphController not available in Lookup");
        }
        this.model = gc.getGraphModel();
    }

    /**
     * Ingest a batch of tweets (poll response). Acquires a single write lock.
     */
    public void processBatch(List<Tweet> tweets, Includes includes, NetworkLogic logic) {
        if (tweets == null || tweets.isEmpty()) return;
        DirectedGraph g = model.getDirectedGraph();
        g.writeLock();
        try {
            ensureColumns();
            Map<String, User> userIndex = indexUsers(includes);
            Map<String, Tweet> tweetIndex = indexTweets(includes);
            for (Tweet t : tweets) {
                logic.processTweet(t, userIndex, tweetIndex, g, model.factory(), this);
            }
        } finally {
            g.writeUnlock();
        }
    }

    /**
     * Ingest a single tweet (streaming message).
     */
    public void processOne(StreamResponse msg, NetworkLogic logic) {
        if (msg == null || msg.data == null) return;
        DirectedGraph g = model.getDirectedGraph();
        g.writeLock();
        try {
            ensureColumns();
            Map<String, User> userIndex = indexUsers(msg.includes);
            Map<String, Tweet> tweetIndex = indexTweets(msg.includes);
            logic.processTweet(msg.data, userIndex, tweetIndex, g, model.factory(), this);
        } finally {
            g.writeUnlock();
        }
    }

    public void clearGraph() {
        DirectedGraph g = model.getDirectedGraph();
        g.writeLock();
        try {
            g.clear();
        } finally {
            g.writeUnlock();
        }
    }

    // --- helpers, called under the write lock ---

    Node getOrCreateUserNode(String userId, User u, DirectedGraph g, GraphFactory factory) {
        if (userId == null) return null;
        Node existing = g.getNode(userId);
        if (existing != null) {
            // Refresh label/attrs if we now have richer user info.
            if (u != null && u.username != null && existing.getAttribute(ATTR_USERNAME) == null) {
                existing.setAttribute(ATTR_USERNAME, u.username);
                existing.setAttribute(ATTR_NAME, u.name);
                existing.setAttribute(ATTR_PROFILE_IMAGE, u.profileImageUrl);
                existing.setLabel("@" + u.username);
            }
            return existing;
        }
        Node n = factory.newNode(userId);
        if (u != null && u.username != null) {
            n.setLabel("@" + u.username);
            n.setAttribute(ATTR_USERNAME, u.username);
            n.setAttribute(ATTR_NAME, u.name);
            n.setAttribute(ATTR_PROFILE_IMAGE, u.profileImageUrl);
        } else {
            n.setLabel(userId);
        }
        g.addNode(n);
        return n;
    }

    void addOrIncrementEdge(Node src, Node dst, String type, DirectedGraph g, GraphFactory factory) {
        if (src == null || dst == null || src.equals(dst)) return;
        String edgeId = src.getId() + ":" + dst.getId() + ":" + type;
        Edge existing = g.getEdge(edgeId);
        if (existing != null) {
            existing.setWeight(existing.getWeight() + 1.0);
            return;
        }
        Edge e = factory.newEdge(edgeId, src, dst, 0, 1.0, true);
        e.setAttribute(ATTR_INTERACTION, type);
        g.addEdge(e);
    }

    private void ensureColumns() {
        if (columnsReady) return;
        Table nodeTable = model.getNodeTable();
        if (!nodeTable.hasColumn(ATTR_USERNAME)) {
            nodeTable.addColumn(ATTR_USERNAME, String.class, Origin.DATA);
        }
        if (!nodeTable.hasColumn(ATTR_NAME)) {
            nodeTable.addColumn(ATTR_NAME, String.class, Origin.DATA);
        }
        if (!nodeTable.hasColumn(ATTR_PROFILE_IMAGE)) {
            nodeTable.addColumn(ATTR_PROFILE_IMAGE, String.class, Origin.DATA);
        }
        Table edgeTable = model.getEdgeTable();
        if (!edgeTable.hasColumn(ATTR_INTERACTION)) {
            edgeTable.addColumn(ATTR_INTERACTION, String.class, Origin.DATA);
        }
        columnsReady = true;
    }

    private static Map<String, User> indexUsers(Includes includes) {
        Map<String, User> idx = new HashMap<>();
        if (includes == null) return idx;
        for (User u : includes.safeUsers()) {
            if (u.id != null) idx.put(u.id, u);
        }
        return idx;
    }

    private static Map<String, Tweet> indexTweets(Includes includes) {
        Map<String, Tweet> idx = new HashMap<>();
        if (includes == null) return idx;
        for (Tweet t : includes.safeTweets()) {
            if (t.id != null) idx.put(t.id, t);
        }
        return idx;
    }

    /** Package-private for tests / debugging. */
    GraphModel model() {
        return model;
    }
}
