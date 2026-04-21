package org.gephi.plugins.xapi.core;

public interface StatusListener {

    enum Level { IDLE, INFO, RUNNING, WARN, ERROR, STOPPED }

    void onStatus(Level level, String message);
}
