package com.aigalgame.demo.live2d;

public final class OfficialLive2DRendererStatus {
    public final boolean sdkCoreLoaded;
    public final boolean modelLoaded;
    public final int drawableCount;
    public final String glLifecycle;
    public final String lastError;
    public final Live2DBootState bootState;
    public final String phase;
    public final long loadElapsedMs;
    public final boolean surfaceAttached;
    public final boolean engineReused;

    public OfficialLive2DRendererStatus(
        boolean sdkCoreLoaded,
        boolean modelLoaded,
        int drawableCount,
        String glLifecycle,
        String lastError,
        Live2DBootState bootState,
        String phase,
        long loadElapsedMs,
        boolean surfaceAttached,
        boolean engineReused
    ) {
        this.sdkCoreLoaded = sdkCoreLoaded;
        this.modelLoaded = modelLoaded;
        this.drawableCount = drawableCount;
        this.glLifecycle = glLifecycle;
        this.lastError = lastError == null ? "" : lastError;
        this.bootState = bootState == null ? Live2DBootState.NotStarted : bootState;
        this.phase = phase == null ? "" : phase;
        this.loadElapsedMs = loadElapsedMs;
        this.surfaceAttached = surfaceAttached;
        this.engineReused = engineReused;
    }

    public static OfficialLive2DRendererStatus initial() {
        return new OfficialLive2DRendererStatus(
            false,
            false,
            0,
            "not-started",
            "",
            Live2DBootState.NotStarted,
            "not-started",
            0L,
            false,
            false
        );
    }

    public String summary() {
        return "SDK/Core loaded=" + sdkCoreLoaded
            + ", model loaded=" + modelLoaded
            + ", drawable count=" + drawableCount
            + ", GL lifecycle=" + glLifecycle
            + ", boot=" + bootState
            + ", phase=" + phase
            + ", load elapsed=" + loadElapsedMs + "ms"
            + ", surface attached=" + surfaceAttached
            + ", engine reused=" + engineReused
            + (lastError.isEmpty() ? "" : ", last error=" + lastError);
    }
}
