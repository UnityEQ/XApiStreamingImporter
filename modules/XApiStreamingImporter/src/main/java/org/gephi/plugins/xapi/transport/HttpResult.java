package org.gephi.plugins.xapi.transport;

public final class HttpResult {

    public final int statusCode;
    public final String body;

    public HttpResult(int statusCode, String body) {
        this.statusCode = statusCode;
        this.body = body;
    }

    public boolean ok() {
        return statusCode >= 200 && statusCode < 300;
    }
}
