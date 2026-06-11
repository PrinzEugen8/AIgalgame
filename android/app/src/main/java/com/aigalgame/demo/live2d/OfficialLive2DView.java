package com.aigalgame.demo.live2d;

import android.content.Context;
import android.graphics.SurfaceTexture;
import android.util.Log;
import android.view.TextureView;

import com.aigalgame.demo.Live2DRenderCommand;

public class OfficialLive2DView extends TextureView implements TextureView.SurfaceTextureListener {
    private static final String TAG = "OfficialLive2DView";

    public interface StageListener {
        void onStatus(OfficialLive2DRendererStatus status);
        void onError(String error);
    }

    private final PersistentLive2DEngine engine;
    private final PersistentLive2DEngine.Listener engineListener;
    private StageListener stageListener;
    private Live2DRenderCommand lastCommand;
    private boolean resumed;
    private boolean stageVisible = true;
    private boolean rendererReady;

    public OfficialLive2DView(Context context) {
        super(context);
        engine = PersistentLive2DEngine.getInstance(context);
        engineListener = new PersistentLive2DEngine.Listener() {
            @Override
            public void onStatus(OfficialLive2DRendererStatus status) {
                rendererReady = status.modelLoaded && status.drawableCount > 0;
                updateSurfaceAlpha();
                if (stageListener != null) {
                    stageListener.onStatus(status);
                }
            }

            @Override
            public void onError(String error) {
                rendererReady = false;
                updateSurfaceAlpha();
                if (stageListener != null) {
                    stageListener.onError(error);
                }
            }
        };
        setOpaque(false);
        setAlpha(0.0f);
        setSurfaceTextureListener(this);
        setClickable(false);
        setFocusable(false);
        engine.setListener(engineListener);
    }

    public void setStageListener(StageListener listener) {
        stageListener = listener;
        engine.setListener(engineListener);
    }

    public void clearStageListener() {
        stageListener = null;
        engine.clearListener(engineListener);
    }

    public void setStageVisible(boolean visible) {
        stageVisible = visible;
        updateSurfaceAlpha();
    }

    public void submitCommand(Live2DRenderCommand command) {
        lastCommand = command;
        engine.submit(command);
    }

    public void releaseRenderer() {
        engine.releaseForProcessExit();
    }

    public void onResume() {
        resumed = true;
        Log.i(TAG, "onResume surface=" + System.identityHashCode(getSurfaceTexture()));
        engine.resumeFrames();
        SurfaceTexture texture = getSurfaceTexture();
        if (texture != null && getWidth() > 0 && getHeight() > 0) {
            engine.attach(texture, getWidth(), getHeight());
            if (lastCommand != null) {
                engine.submit(lastCommand);
            }
        }
    }

    public void onPause() {
        resumed = false;
        Log.i(TAG, "onPause keep renderer");
        engine.pauseFrames();
    }

    @Override
    public void onSurfaceTextureAvailable(SurfaceTexture surface, int width, int height) {
        Log.i(TAG, "surface available=" + System.identityHashCode(surface) + " size=" + width + "x" + height);
        if (resumed) {
            engine.attach(surface, width, height);
            if (lastCommand != null) {
                engine.submit(lastCommand);
            }
        }
    }

    @Override
    public void onSurfaceTextureSizeChanged(SurfaceTexture surface, int width, int height) {
        Log.i(TAG, "surface size changed=" + System.identityHashCode(surface) + " size=" + width + "x" + height);
        engine.attach(surface, width, height);
    }

    @Override
    public boolean onSurfaceTextureDestroyed(SurfaceTexture surface) {
        // Keep the SurfaceTexture and EGL thread alive across short background trips.
        // The persistent engine is released only by explicit process-level teardown.
        Log.i(TAG, "surface destroyed callback kept=" + System.identityHashCode(surface));
        return false;
    }

    @Override
    public void onSurfaceTextureUpdated(SurfaceTexture surface) {
        // No-op.
    }

    @Override
    protected void onDetachedFromWindow() {
        Log.i(TAG, "detached from window; listener cleared only");
        clearStageListener();
        super.onDetachedFromWindow();
    }

    private void updateSurfaceAlpha() {
        setAlpha(stageVisible && rendererReady ? 1.0f : 0.0f);
    }
}
