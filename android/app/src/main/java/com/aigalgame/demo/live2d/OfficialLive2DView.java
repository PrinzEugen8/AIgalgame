package com.aigalgame.demo.live2d;

import android.content.Context;
import android.graphics.SurfaceTexture;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLSurface;
import android.opengl.GLUtils;
import android.os.Handler;
import android.os.Looper;
import android.view.Surface;
import android.view.TextureView;

import com.aigalgame.demo.Live2DRenderCommand;

public class OfficialLive2DView extends TextureView implements TextureView.SurfaceTextureListener {
    public interface StageListener {
        void onStatus(OfficialLive2DRendererStatus status);
        void onError(String error);
    }

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private StageListener stageListener;
    private RenderThread renderThread;
    private Live2DRenderCommand lastCommand;
    private boolean resumed;

    public OfficialLive2DView(Context context) {
        super(context);
        setOpaque(false);
        setAlpha(0.0f);
        setSurfaceTextureListener(this);
        setClickable(false);
        setFocusable(false);
    }

    public void setStageListener(StageListener listener) {
        stageListener = listener;
    }

    public void submitCommand(Live2DRenderCommand command) {
        lastCommand = command;
        RenderThread thread = renderThread;
        if (thread != null) {
            thread.submitCommand(command);
        }
    }

    public void releaseRenderer() {
        stopRenderThread();
    }

    public void onResume() {
        resumed = true;
        SurfaceTexture texture = getSurfaceTexture();
        if (texture != null && renderThread == null && getWidth() > 0 && getHeight() > 0) {
            startRenderThread(texture, getWidth(), getHeight());
        }
    }

    public void onPause() {
        resumed = false;
        stopRenderThread();
    }

    @Override
    public void onSurfaceTextureAvailable(SurfaceTexture surface, int width, int height) {
        if (resumed) {
            startRenderThread(surface, width, height);
        }
    }

    @Override
    public void onSurfaceTextureSizeChanged(SurfaceTexture surface, int width, int height) {
        RenderThread thread = renderThread;
        if (thread != null) {
            thread.updateSize(width, height);
        }
    }

    @Override
    public boolean onSurfaceTextureDestroyed(SurfaceTexture surface) {
        stopRenderThread();
        return true;
    }

    @Override
    public void onSurfaceTextureUpdated(SurfaceTexture surface) {
        // No-op.
    }

    private void startRenderThread(SurfaceTexture texture, int width, int height) {
        if (renderThread != null) {
            return;
        }
        RenderThread thread = new RenderThread(
            getContext().getApplicationContext(),
            texture,
            Math.max(width, 1),
            Math.max(height, 1),
            new OfficialLive2DRenderer.Callback() {
                @Override
                public void onStatus(final OfficialLive2DRendererStatus status) {
                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            if (status.modelLoaded && status.drawableCount > 0) {
                                setAlpha(1.0f);
                            }
                            if (stageListener != null) {
                                stageListener.onStatus(status);
                            }
                        }
                    });
                }

                @Override
                public void onError(final String error) {
                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            setAlpha(0.0f);
                            if (stageListener != null) {
                                stageListener.onError(error);
                            }
                        }
                    });
                }
            }
        );
        renderThread = thread;
        thread.start();
        if (lastCommand != null) {
            thread.submitCommand(lastCommand);
        }
    }

    private void stopRenderThread() {
        RenderThread thread = renderThread;
        renderThread = null;
        if (thread != null) {
            thread.shutdown();
            if (Thread.currentThread() != thread) {
                try {
                    thread.join(1200L);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            }
        }
    }

    private static final class RenderThread extends Thread {
        private static final long IDLE_FRAME_MS = 34L;
        private static final long FAST_FRAME_MS = 17L;

        private final Context appContext;
        private final SurfaceTexture surfaceTexture;
        private final OfficialLive2DRenderer renderer;
        private final OfficialLive2DRenderer.Callback callback;
        private final Object frameLock = new Object();
        private volatile boolean running = true;
        private volatile boolean fastFrameMode;
        private volatile int width;
        private volatile int height;
        private int appliedWidth;
        private int appliedHeight;

        private EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
        private EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
        private EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;
        private Surface surface;

        RenderThread(
            Context context,
            SurfaceTexture surfaceTexture,
            int width,
            int height,
            OfficialLive2DRenderer.Callback callback
        ) {
            super("OfficialLive2DTexture");
            appContext = context.getApplicationContext();
            this.surfaceTexture = surfaceTexture;
            this.width = width;
            this.height = height;
            this.callback = callback;
            renderer = new OfficialLive2DRenderer(appContext, callback);
        }

        void submitCommand(Live2DRenderCommand command) {
            renderer.submitCommand(command);
            fastFrameMode = command != null && (command.getSpeaking() || command.getMouthOpen() > 0.02f);
            requestFrame();
        }

        void updateSize(int width, int height) {
            this.width = Math.max(width, 1);
            this.height = Math.max(height, 1);
            requestFrame();
        }

        void shutdown() {
            running = false;
            requestFrame();
        }

        @Override
        public void run() {
            try {
                initEgl();
                renderer.onSurfaceCreated(null, null);
                appliedWidth = width;
                appliedHeight = height;
                renderer.onSurfaceChanged(null, appliedWidth, appliedHeight);

                while (running) {
                    if (appliedWidth != width || appliedHeight != height) {
                        appliedWidth = width;
                        appliedHeight = height;
                        renderer.onSurfaceChanged(null, appliedWidth, appliedHeight);
                    }
                    renderer.onDrawFrame(null);
                    EGL14.eglSwapBuffers(eglDisplay, eglSurface);
                    synchronized (frameLock) {
                        frameLock.wait(fastFrameMode ? FAST_FRAME_MS : IDLE_FRAME_MS);
                    }
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } catch (Throwable t) {
                if (callback != null) {
                    callback.onError("Live2D TextureView EGL failed: " + t.getMessage());
                }
            } finally {
                renderer.release();
                releaseEgl();
            }
        }

        private void requestFrame() {
            synchronized (frameLock) {
                frameLock.notifyAll();
            }
        }

        private void initEgl() {
            eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
            if (eglDisplay == EGL14.EGL_NO_DISPLAY) {
                throw new IllegalStateException("eglGetDisplay failed: " + GLUtils.getEGLErrorString(EGL14.eglGetError()));
            }
            int[] version = new int[2];
            if (!EGL14.eglInitialize(eglDisplay, version, 0, version, 1)) {
                throw new IllegalStateException("eglInitialize failed: " + GLUtils.getEGLErrorString(EGL14.eglGetError()));
            }

            int[] configAttributes = {
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT,
                EGL14.EGL_RED_SIZE, 8,
                EGL14.EGL_GREEN_SIZE, 8,
                EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_DEPTH_SIZE, 16,
                EGL14.EGL_NONE
            };
            EGLConfig[] configs = new EGLConfig[1];
            int[] numConfigs = new int[1];
            if (!EGL14.eglChooseConfig(eglDisplay, configAttributes, 0, configs, 0, configs.length, numConfigs, 0)) {
                throw new IllegalStateException("eglChooseConfig failed: " + GLUtils.getEGLErrorString(EGL14.eglGetError()));
            }

            int[] contextAttributes = {
                EGL14.EGL_CONTEXT_CLIENT_VERSION, 2,
                EGL14.EGL_NONE
            };
            eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT, contextAttributes, 0);
            if (eglContext == EGL14.EGL_NO_CONTEXT) {
                throw new IllegalStateException("eglCreateContext failed: " + GLUtils.getEGLErrorString(EGL14.eglGetError()));
            }

            surfaceTexture.setDefaultBufferSize(Math.max(width, 1), Math.max(height, 1));
            surface = new Surface(surfaceTexture);
            int[] surfaceAttributes = { EGL14.EGL_NONE };
            eglSurface = EGL14.eglCreateWindowSurface(eglDisplay, configs[0], surface, surfaceAttributes, 0);
            if (eglSurface == EGL14.EGL_NO_SURFACE) {
                throw new IllegalStateException("eglCreateWindowSurface failed: " + GLUtils.getEGLErrorString(EGL14.eglGetError()));
            }

            if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
                throw new IllegalStateException("eglMakeCurrent failed: " + GLUtils.getEGLErrorString(EGL14.eglGetError()));
            }
        }

        private void releaseEgl() {
            if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
                EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                if (eglSurface != EGL14.EGL_NO_SURFACE) {
                    EGL14.eglDestroySurface(eglDisplay, eglSurface);
                }
                if (eglContext != EGL14.EGL_NO_CONTEXT) {
                    EGL14.eglDestroyContext(eglDisplay, eglContext);
                }
                EGL14.eglReleaseThread();
                EGL14.eglTerminate(eglDisplay);
            }
            eglDisplay = EGL14.EGL_NO_DISPLAY;
            eglContext = EGL14.EGL_NO_CONTEXT;
            eglSurface = EGL14.EGL_NO_SURFACE;
            if (surface != null) {
                surface.release();
                surface = null;
            }
        }
    }
}
