package org.gephi.plugins.xapi.core;

public final class SessionConfig {

    public final String keyword;
    public final TransportKind transport;
    public final String bearerToken;   // only used when transport == JAVA_HTTP
    public final DataPath dataPath;
    public final int pollIntervalSeconds;

    public SessionConfig(String keyword,
                         TransportKind transport,
                         String bearerToken,
                         DataPath dataPath,
                         int pollIntervalSeconds) {
        this.keyword = keyword;
        this.transport = transport;
        this.bearerToken = bearerToken;
        this.dataPath = dataPath;
        this.pollIntervalSeconds = pollIntervalSeconds;
    }
}
