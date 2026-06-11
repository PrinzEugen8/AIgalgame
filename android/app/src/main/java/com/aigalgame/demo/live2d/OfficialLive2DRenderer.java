package com.aigalgame.demo.live2d;

import android.content.Context;
import android.opengl.GLES20;
import android.opengl.GLSurfaceView;
import android.util.Log;

import com.aigalgame.demo.Live2DRenderCommand;
import com.aigalgame.demo.OutfitPlacement;
import com.live2d.sdk.cubism.core.ICubismLogger;
import com.live2d.sdk.cubism.core.Live2DCubismCore;
import com.live2d.sdk.cubism.framework.CubismFramework;
import com.live2d.sdk.cubism.framework.CubismFrameworkConfig;
import com.live2d.sdk.cubism.framework.ICubismLoadFileFunction;
import com.live2d.sdk.cubism.framework.math.CubismMatrix44;
import com.live2d.sdk.cubism.framework.rendering.android.CubismOffscreenManagerAndroid;
import com.live2d.sdk.cubism.framework.rendering.android.CubismShaderAndroid;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;

import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

public final class OfficialLive2DRenderer implements GLSurfaceView.Renderer {
    public interface Callback {
        void onStatus(OfficialLive2DRendererStatus status);
        void onError(String error);
    }

    private static final String TAG = "OfficialLive2D";
    private static final String MODEL3_PATH = "live2d/models/neko/neko.model3.json";
    private static final Object FRAMEWORK_LOCK = new Object();

    private final Context appContext;
    private final Callback callback;
    private final CubismMatrix44 projection = CubismMatrix44.create();

    private volatile Live2DRenderCommand currentCommand = new Live2DRenderCommand(
        "neko",
        "calm",
        "idle",
        0.0f,
        false,
        0.0f,
        0.0f,
        new OutfitPlacement(1.12f, 0.0f, 0.0f, 48.0f),
        true,
        0L
    );

    private NekoLive2DModel model;
    private int surfaceWidth;
    private int surfaceHeight;
    private long lastFrameNanos;
    private boolean loadAttempted;
    private boolean coreLoaded;
    private String glLifecycle = "created";
    private String lastError = "";

    public OfficialLive2DRenderer(Context context, Callback callback) {
        appContext = context.getApplicationContext();
        this.callback = callback;
    }

    public void submitCommand(Live2DRenderCommand command) {
        if (command != null) {
            currentCommand = command;
        }
    }

    @Override
    public void onSurfaceCreated(GL10 unused, EGLConfig config) {
        glLifecycle = "surface-created";
        loadAttempted = false;
        lastFrameNanos = 0L;
        releaseModel();

        try {
            ensureCubismFramework();
            coreLoaded = true;
            CubismShaderAndroid.getInstance().releaseInvalidShaderProgram();
            CubismShaderAndroid.deleteInstance();
            GLES20.glEnable(GLES20.GL_BLEND);
            GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA);
            GLES20.glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
            reportStatus();
        } catch (Throwable t) {
            reportError("SDK/Core load failed: " + t.getMessage());
        }
    }

    @Override
    public void onSurfaceChanged(GL10 unused, int width, int height) {
        surfaceWidth = Math.max(width, 1);
        surfaceHeight = Math.max(height, 1);
        glLifecycle = "surface-changed-" + surfaceWidth + "x" + surfaceHeight;
        GLES20.glViewport(0, 0, surfaceWidth, surfaceHeight);
        if (model != null) {
            model.setRenderTargetSize(surfaceWidth, surfaceHeight);
        }
        reportStatus();
    }

    @Override
    public void onDrawFrame(GL10 unused) {
        GLES20.glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT | GLES20.GL_DEPTH_BUFFER_BIT);
        if (!coreLoaded || surfaceWidth <= 0 || surfaceHeight <= 0) {
            return;
        }

        if (model == null) {
            if (loadAttempted) {
                return;
            }
            loadAttempted = true;
            try {
                model = new NekoLive2DModel(appContext);
                model.loadAssets(MODEL3_PATH, surfaceWidth, surfaceHeight);
                model.setRenderTargetSize(surfaceWidth, surfaceHeight);
                reportStatus();
            } catch (Throwable t) {
                releaseModel();
                reportError("NEKO model load failed: " + t.getMessage());
                return;
            }
        }

        final long now = System.nanoTime();
        final float deltaSeconds = lastFrameNanos == 0L
            ? 1.0f / 60.0f
            : Math.min(0.05f, (now - lastFrameNanos) / 1_000_000_000.0f);
        lastFrameNanos = now;

        try {
            CubismOffscreenManagerAndroid.getInstance().beginFrameProcess();
            model.update(currentCommand, deltaSeconds);
            configureProjection(currentCommand);
            model.draw(projection);
            CubismOffscreenManagerAndroid.getInstance().endFrameProcess();
            CubismOffscreenManagerAndroid.getInstance().releaseStaleRenderTextures();
        } catch (Throwable t) {
            reportError("Live2D draw failed: " + t.getMessage());
        }
    }

    public void release() {
        releaseModel();
        CubismOffscreenManagerAndroid.releaseInstance();
        glLifecycle = "released";
        reportStatus();
    }

    private void configureProjection(Live2DRenderCommand command) {
        projection.loadIdentity();
        if (model == null) {
            return;
        }
        final float aspectRatio = (float) surfaceWidth / (float) surfaceHeight;
        final float displayRatio = (float) surfaceHeight / (float) surfaceWidth;
        final float canvasRatio = model.getModel().getCanvasHeight() / model.getModel().getCanvasWidth();
        model.configureModelMatrix(command.getPlacement(), surfaceWidth, surfaceHeight, canvasRatio, displayRatio);
        if (canvasRatio < displayRatio) {
            projection.scale(1.0f, aspectRatio);
        } else {
            projection.scale(1.0f / aspectRatio, 1.0f);
        }
    }

    private void releaseModel() {
        if (model != null) {
            model.deleteModel();
            model = null;
        }
    }

    private void ensureCubismFramework() {
        synchronized (FRAMEWORK_LOCK) {
            if (!CubismFramework.isStarted()) {
                CubismFramework.Option option = new CubismFramework.Option();
                option.logFunction = new ICubismLogger() {
                    @Override
                    public void print(String message) {
                        Log.d(TAG, message == null ? "" : message);
                    }
                };
                option.loggingLevel = CubismFrameworkConfig.LogLevel.INFO;
                option.loadFileFunction = new AssetLoadFileFunction(appContext);
                CubismFramework.startUp(option);
            }
            Live2DCubismCore.getVersion();
            if (!CubismFramework.isInitialized()) {
                CubismFramework.initialize();
            }
        }
    }

    private void reportStatus() {
        if (callback == null) {
            return;
        }
        callback.onStatus(new OfficialLive2DRendererStatus(
            coreLoaded,
            model != null && model.isInitialized(),
            model == null ? 0 : model.getDrawableCount(),
            glLifecycle,
            lastError
        ));
    }

    private void reportError(String message) {
        lastError = message == null ? "Live2D renderer error" : message;
        Log.e(TAG, lastError);
        reportStatus();
        if (callback != null) {
            callback.onError(lastError);
        }
    }

    private static final class AssetLoadFileFunction implements ICubismLoadFileFunction {
        private final Context context;

        AssetLoadFileFunction(Context context) {
            this.context = context.getApplicationContext();
        }

        @Override
        public byte[] load(String path) {
            return readAsset(context, path);
        }
    }

    static byte[] readAsset(Context context, String path) {
        if (path == null || path.isEmpty()) {
            return null;
        }
        try (InputStream input = context.getAssets().open(path);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        } catch (IOException e) {
            Log.e(TAG, "Asset load failed: " + path, e);
            return null;
        }
    }
}
