package com.aigalgame.demo.live2d;

public final class OfficialLive2DRendererStatus {
    public final boolean sdkCoreLoaded;
    public final boolean modelLoaded;
    public final int drawableCount;
    public final String glLifecycle;
    public final String lastError;

    public OfficialLive2DRendererStatus(
        boolean sdkCoreLoaded,
        boolean modelLoaded,
        int drawableCount,
        String glLifecycle,
        String lastError
    ) {
        this.sdkCoreLoaded = sdkCoreLoaded;
        this.modelLoaded = modelLoaded;
        this.drawableCount = drawableCount;
        this.glLifecycle = glLifecycle;
        this.lastError = lastError == null ? "" : lastError;
    }

    public String summary() {
        return "SDK/Core loaded=" + sdkCoreLoaded
            + ", model loaded=" + modelLoaded
            + ", drawable count=" + drawableCount
            + ", GL lifecycle=" + glLifecycle
            + (lastError.isEmpty() ? "" : ", last error=" + lastError);
    }
}
