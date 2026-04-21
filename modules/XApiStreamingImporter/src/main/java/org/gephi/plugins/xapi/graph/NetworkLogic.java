package org.gephi.plugins.xapi.graph;

import java.util.Map;
import org.gephi.graph.api.DirectedGraph;
import org.gephi.graph.api.GraphFactory;
import org.gephi.plugins.xapi.model.Tweet;
import org.gephi.plugins.xapi.model.User;

/**
 * Strategy for mapping one tweet into graph mutations. Called under the graph write lock.
 * Implementations must use only the provided graph/factory and the GraphWriter helpers.
 */
public interface NetworkLogic {

    void processTweet(Tweet tweet,
                      Map<String, User> userIndex,
                      Map<String, Tweet> tweetIndex,
                      DirectedGraph graph,
                      GraphFactory factory,
                      GraphWriter writer);
}
