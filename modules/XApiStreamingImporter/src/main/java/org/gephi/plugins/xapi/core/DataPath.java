package org.gephi.plugins.xapi.core;

public enum DataPath {
    /** Poll GET /2/tweets/search/recent on an interval (Free tier friendly). */
    POLLING,
    /** Long-lived connection to GET /2/tweets/search/stream with rules (Basic+ tier). */
    FILTERED_STREAM
}
