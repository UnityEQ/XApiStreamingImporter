package org.gephi.plugins.xapi.graph;

import java.util.Map;
import org.gephi.graph.api.DirectedGraph;
import org.gephi.graph.api.GraphFactory;
import org.gephi.graph.api.Node;
import org.gephi.plugins.xapi.model.Mention;
import org.gephi.plugins.xapi.model.ReferencedTweet;
import org.gephi.plugins.xapi.model.Tweet;
import org.gephi.plugins.xapi.model.User;

/**
 * User-interaction network. Per tweet matching the user's keyword:
 *   - author node ensured
 *   - MENTIONS edge author -> each mentioned user
 *   - REPLY / RETWEET / QUOTE edge author -> referenced tweet's author (resolved via includes)
 * Edges are weighted by count.
 */
public final class UserNetworkLogic implements NetworkLogic {

    public static final String TYPE_MENTIONS = "MENTIONS";
    public static final String TYPE_REPLY = "REPLY";
    public static final String TYPE_RETWEET = "RETWEET";
    public static final String TYPE_QUOTE = "QUOTE";

    @Override
    public void processTweet(Tweet tweet,
                             Map<String, User> userIndex,
                             Map<String, Tweet> tweetIndex,
                             DirectedGraph graph,
                             GraphFactory factory,
                             GraphWriter writer) {
        if (tweet == null || tweet.authorId == null) return;

        User authorUser = userIndex.get(tweet.authorId);
        Node author = writer.getOrCreateUserNode(tweet.authorId, authorUser, graph, factory);
        if (author == null) return;

        for (Mention m : tweet.mentions()) {
            if (m == null || m.id == null) continue;
            User mu = userIndex.get(m.id);
            if (mu == null && m.username != null) {
                mu = new User();
                mu.id = m.id;
                mu.username = m.username;
            }
            Node target = writer.getOrCreateUserNode(m.id, mu, graph, factory);
            writer.addOrIncrementEdge(author, target, TYPE_MENTIONS, graph, factory);
        }

        for (ReferencedTweet ref : tweet.referenced()) {
            if (ref == null || ref.id == null || ref.type == null) continue;
            Tweet refTweet = tweetIndex.get(ref.id);
            if (refTweet == null || refTweet.authorId == null) continue;
            User refAuthor = userIndex.get(refTweet.authorId);
            Node target = writer.getOrCreateUserNode(refTweet.authorId, refAuthor, graph, factory);
            String edgeType = mapRefType(ref.type);
            writer.addOrIncrementEdge(author, target, edgeType, graph, factory);
        }
    }

    private static String mapRefType(String apiType) {
        switch (apiType) {
            case ReferencedTweet.TYPE_REPLY: return TYPE_REPLY;
            case ReferencedTweet.TYPE_RETWEET: return TYPE_RETWEET;
            case ReferencedTweet.TYPE_QUOTE: return TYPE_QUOTE;
            default: return apiType.toUpperCase();
        }
    }
}
