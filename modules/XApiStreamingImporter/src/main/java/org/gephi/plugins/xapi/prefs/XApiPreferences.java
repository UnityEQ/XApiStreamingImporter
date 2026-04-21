package org.gephi.plugins.xapi.prefs;

import java.util.prefs.Preferences;
import org.openide.util.NbPreferences;

/**
 * Non-sensitive UI state only. The bearer token is intentionally NOT persisted —
 * users paste it every session.
 */
public final class XApiPreferences {

    private static final String KEY_KEYWORD = "x.api.keyword";
    private static final String KEY_INTERVAL = "x.api.interval";
    private static final String KEY_TRANSPORT = "x.api.transport";
    private static final String KEY_DATA_PATH = "x.api.dataPath";

    // Legacy key kept only so we can scrub any token left over from earlier builds.
    private static final String LEGACY_KEY_BEARER = "x.api.bearer";
    private static final String LEGACY_KEY_SAVE_TOKEN = "x.api.saveToken";

    private XApiPreferences() {
    }

    private static Preferences prefs() {
        return NbPreferences.forModule(XApiPreferences.class);
    }

    /** Delete any token previously persisted by an older build. Safe to call every startup. */
    public static void scrubLegacyToken() {
        Preferences p = prefs();
        p.remove(LEGACY_KEY_BEARER);
        p.remove(LEGACY_KEY_SAVE_TOKEN);
    }

    public static String getKeyword() { return prefs().get(KEY_KEYWORD, ""); }
    public static void setKeyword(String v) { prefs().put(KEY_KEYWORD, v == null ? "" : v); }

    public static int getInterval() { return prefs().getInt(KEY_INTERVAL, 30); }
    public static void setInterval(int v) { prefs().putInt(KEY_INTERVAL, v); }

    public static String getTransport() { return prefs().get(KEY_TRANSPORT, "JAVA_HTTP"); }
    public static void setTransport(String v) { prefs().put(KEY_TRANSPORT, v); }

    public static String getDataPath() { return prefs().get(KEY_DATA_PATH, "POLLING"); }
    public static void setDataPath(String v) { prefs().put(KEY_DATA_PATH, v); }
}
