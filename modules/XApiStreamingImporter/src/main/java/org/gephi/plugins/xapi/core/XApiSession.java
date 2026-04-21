package org.gephi.plugins.xapi.core;

import java.util.concurrent.atomic.AtomicBoolean;
import org.gephi.plugins.xapi.graph.GraphWriter;
import org.gephi.plugins.xapi.graph.NetworkLogic;
import org.gephi.plugins.xapi.graph.UserNetworkLogic;
import org.gephi.plugins.xapi.transport.JavaHttpTransport;
import org.gephi.plugins.xapi.transport.XApiTransport;
import org.gephi.plugins.xapi.transport.XurlTransport;

/**
 * Singleton lifecycle controller. Holds one background thread running either
 * a {@link PollingTask} or a {@link StreamTask}. Thread-safe start/stop.
 */
public final class XApiSession {

    private static final XApiSession INSTANCE = new XApiSession();

    public static XApiSession getDefault() {
        return INSTANCE;
    }

    private final Object lock = new Object();
    private Thread worker;
    private XApiTransport transport;
    private AtomicBoolean cancelled;

    private XApiSession() {
    }

    public boolean isRunning() {
        synchronized (lock) {
            return worker != null && worker.isAlive();
        }
    }

    public void start(SessionConfig cfg, StatusListener listener) {
        synchronized (lock) {
            if (isRunning()) {
                listener.onStatus(StatusListener.Level.WARN, "Session already running");
                return;
            }
            this.cancelled = new AtomicBoolean(false);
            this.transport = buildTransport(cfg);
            GraphWriter writer = new GraphWriter();
            NetworkLogic logic = new UserNetworkLogic();

            Runnable job;
            if (cfg.dataPath == DataPath.FILTERED_STREAM) {
                job = new StreamTask(cfg, transport, writer, logic, listener, cancelled);
            } else {
                job = new PollingTask(cfg, transport, writer, logic, listener, cancelled);
            }

            Thread t = new Thread(job, "XApi-Session");
            t.setDaemon(true);
            this.worker = t;
            t.start();
            listener.onStatus(StatusListener.Level.RUNNING,
                    "Started " + (cfg.dataPath == DataPath.POLLING ? "polling" : "streaming")
                            + " for query: " + cfg.keyword);
        }
    }

    public void stop() {
        Thread t;
        XApiTransport tx;
        synchronized (lock) {
            if (cancelled != null) cancelled.set(true);
            t = worker;
            tx = transport;
            worker = null;
            transport = null;
        }
        if (tx != null) {
            try { tx.close(); } catch (Exception ignored) { }
        }
        if (t != null) {
            t.interrupt();
        }
    }

    private static XApiTransport buildTransport(SessionConfig cfg) {
        if (cfg.transport == TransportKind.XURL) {
            return new XurlTransport();
        }
        if (cfg.bearerToken == null || cfg.bearerToken.isEmpty()) {
            throw new IllegalArgumentException("Bearer token is required when transport = Java HTTP");
        }
        return new JavaHttpTransport(cfg.bearerToken);
    }
}
