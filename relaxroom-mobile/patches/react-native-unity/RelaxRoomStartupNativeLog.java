package com.azesmwayreactnativeunity;

import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

public final class RelaxRoomStartupNativeLog {
    private static final String TAG = "RelaxRoomStartup";
    private static final long HEARTBEAT_INTERVAL_MS = 2000L;
    private static long nativeStartMs = -1L;
    private static long playerCtorEndMs = -1L;
    private static int loadingHeartbeatCount = 0;
    private static boolean unityRuntimeReady;
    private static Handler heartbeatHandler;
    private static final Runnable heartbeatRunnable = new Runnable() {
        @Override
        public void run() {
            if (unityRuntimeReady || nativeStartMs < 0L) {
                return;
            }

            loadingHeartbeatCount += 1;
            long sinceCtorMs = playerCtorEndMs >= 0L ? elapsedMs() - playerCtorEndMs : -1L;
            String detail = "tick=" + loadingHeartbeatCount;
            if (sinceCtorMs >= 0L) {
                detail += ",since_ctor_ms=" + sinceCtorMs;
            }
            mark("native_il2cpp_loading", detail);
            scheduleLoadingHeartbeat();
        }
    };

    private RelaxRoomStartupNativeLog() {
    }

    public static void beginNativeTiming() {
        nativeStartMs = SystemClock.elapsedRealtime();
        playerCtorEndMs = -1L;
        loadingHeartbeatCount = 0;
        unityRuntimeReady = false;
        stopLoadingHeartbeat();
        mark("native_create_player_start");
    }

    public static void beginNativeTimingIfNeeded() {
        if (nativeStartMs >= 0L) {
            return;
        }
        beginNativeTiming();
    }

    public static void markPlayerCtorEnd(String detail) {
        playerCtorEndMs = elapsedMs();
        mark("native_player_ctor_end", detail);
        scheduleLoadingHeartbeat();
    }

    public static void onUnityStageReceived(String stage, String detail) {
        if (stage == null) {
            return;
        }

        if (!unityRuntimeReady) {
            if ("process_start".equals(stage) || "startup_timeline".equals(stage)) {
                unityRuntimeReady = true;
                stopLoadingHeartbeat();
                mark("unity_runtime_ready", "stage=" + stage);
            } else {
                markNativeOnly("native_unity_stage", stage + (detail != null && !detail.isEmpty() ? "," + detail : ""));
            }
        }
    }

    public static void onUnityStageReceived(String stage) {
        onUnityStageReceived(stage, null);
    }

    public static void mark(String stage) {
        mark(stage, null);
    }

    public static void mark(String stage, String detail) {
        long elapsedMs = elapsedMs();

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

    private static void markNativeOnly(String stage, String detail) {
        long elapsedMs = elapsedMs();
        String suffix = detail != null && !detail.isEmpty() ? " detail=" + detail : "";
        Log.i(TAG, "[RelaxRoomStartup] layer=native stage=" + stage + " elapsed_ms=" + elapsedMs + suffix);
    }

    private static long elapsedMs() {
        return nativeStartMs >= 0L
            ? SystemClock.elapsedRealtime() - nativeStartMs
            : SystemClock.elapsedRealtime();
    }

    private static void scheduleLoadingHeartbeat() {
        if (unityRuntimeReady || playerCtorEndMs < 0L) {
            return;
        }

        if (heartbeatHandler == null) {
            heartbeatHandler = new Handler(Looper.getMainLooper());
        }

        heartbeatHandler.removeCallbacks(heartbeatRunnable);
        heartbeatHandler.postDelayed(heartbeatRunnable, HEARTBEAT_INTERVAL_MS);
    }

    private static void stopLoadingHeartbeat() {
        if (heartbeatHandler != null) {
            heartbeatHandler.removeCallbacks(heartbeatRunnable);
        }
    }

    private static String escapeJson(String value) {
        return value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"");
    }
}
