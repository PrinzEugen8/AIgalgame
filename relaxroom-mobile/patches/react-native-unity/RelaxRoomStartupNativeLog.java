package com.azesmwayreactnativeunity;

import android.os.SystemClock;
import android.util.Log;

public final class RelaxRoomStartupNativeLog {
    private static final String TAG = "RelaxRoomStartup";
    private static long nativeStartMs = -1L;

    private RelaxRoomStartupNativeLog() {
    }

    public static void beginNativeTiming() {
        nativeStartMs = SystemClock.elapsedRealtime();
        mark("native_create_player_start");
    }

    public static void mark(String stage) {
        mark(stage, null);
    }

    public static void mark(String stage, String detail) {
        long elapsedMs = nativeStartMs >= 0L
            ? SystemClock.elapsedRealtime() - nativeStartMs
            : SystemClock.elapsedRealtime();

        String suffix = detail != null && !detail.isEmpty() ? " detail=" + detail : "";
        Log.i(TAG, "[RelaxRoomStartup] layer=native stage=" + stage + " elapsed_ms=" + elapsedMs + suffix);

        String json = "{\"evt\":\"startup_milestone\",\"source\":\"native\",\"stage\":\""
            + escapeJson(stage)
            + "\",\"elapsed_ms\":"
            + elapsedMs;

        if (detail != null && !detail.isEmpty()) {
            json += ",\"detail\":\"" + escapeJson(detail) + "\"";
        }

        json += "}";
        ReactNativeUnityViewManager.sendMessageToMobileApp(json);
    }

    private static String escapeJson(String value) {
        return value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"");
    }
}
