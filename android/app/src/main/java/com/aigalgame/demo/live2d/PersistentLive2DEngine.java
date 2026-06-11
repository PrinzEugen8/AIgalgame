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
import android.util.Log;
import android.view.Surface;

import com.aigalgame.demo.Live2DRenderCommand;

public final class PersistentLive2DEngine {
    private static final String TAG = "PersistentLive2DEngine";

    public interface Listener {
        void onStatus(OfficialLive2DRendererStatus status);
        void onError(String error);
    }

    private static final Object INSTANCE_LOCK = new Object();
    private static PersistentLive2DEngine instance;

    public static PersistentLive2DEngine getInstance(Context context) {
        synchronized (INSTANCE_LOCK) {
            if (instance == null) {
                instance = new PersistentLive2DEngine(context.getApplicationContext());
            }
            return instance;
        }
    }

    private final Context appContext;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Object lock = new Object();

    private RenderThread renderThread;
    private Listener listener;
    private Live2DRenderCommand lastCommand;
    private OfficialLive2DRendererStatus lastStatus = OfficialLive2DRendererStatus.initial();
    private boolean engineWasCreated;

    private PersistentLive2DEngine(Context context) {
        appContext = context.getApplicationContext();
    }

    public void setListener(Listener listener) {
        OfficialLive2DRendererStatus status;
        synchronized (lock) {
            this.listener = listener;
            status = lastStatus;
        }
        dispatchStatus(status);
    }

    public void clearListener(Listener listener) {
        synchronized (lock) {
            if (this.listener == listener) {
                this.listener = null;
            }
        }
    }

    public void attach(SurfaceTexture texture, int width, int height) {
        if (texture == null) {
            return;
        }
        RenderThread threadToStart = null;
        RenderThread threadToStop = null;
        Live2DRenderCommand commandToSubmit;
        synchronized (lock) {
            if (renderThread != null && renderThread.uses(texture)) {
                Log.i(TAG, "attach reused surface=" + System.identityHashCode(texture) + " size=" + width + "x" + height);
                renderThread.updateSize(width, height);
                renderThread.setPaused(false);
                commandToSubmit = lastCommand;
            } else {
                Log.i(
                    TAG,
                    "attach new surface=" + System.identityHashCode(texture)
                        + " previousThread=" + (renderThread != null)
                        + " size=" + width + "x" + height
                );
                threadToStop = takeRenderThreadLocked();
                boolean reused = engineWasCreated || lastStatus.bootState == Live2DBootState.Ready;
                renderThread = new RenderThread(appContext, texture, Math.max(width, 1), Math.max(height, 1), reused, new EngineCallback());
                threadToStart = renderThread;
                engineWasCreated = true;
                commandToSubmit = lastCommand;
            }
        }
        joinRenderThread(threadToStop);
        if (threadToStart != null) {
            threadToStart.start();
        }
        if (commandToSubmit != null) {
            submit(commandToSubmit);
        }
    }

    public void detachSurface(SurfaceTexture texture) {
        RenderThread threadToStop = null;
        synchronized (lock) {
            if (renderThread != null && renderThread.uses(texture)) {
                Log.i(TAG, "detach surface=" + System.identityHashCode(texture));
                threadToStop = takeRenderThreadLocked();
            }
        }
        joinRenderThread(threadToStop);
    }

    public void pauseFrames() {
        synchronized (lock) {
            if (renderThread != null) {
                renderThread.setPaused(true);
            }
        }
    }

    public void resumeFrames() {
        synchronized (lock) {
            if (renderThread != null) {
                renderThread.setPaused(false);
            }
        }
    }

    public void submit(Live2DRenderCommand command) {
        if (command == null) {
            return;
        }
        synchronized (lock) {
            lastCommand = command;
            if (renderThread != null) {
                renderThread.submitCommand(command);
            }
        }
    }

    public void releaseForProcessExit() {
        RenderThread threadToStop;
        synchronized (lock) {
            Log.i(TAG, "releaseForProcessExit");
            threadToStop = takeRenderThreadLocked();
            lastStatus = OfficialLive2DRendererStatus.initial();
            engineWasCreated = false;
        }
        joinRenderThread(threadToStop);
        dispatchStatus(lastStatus);
    }

    private RenderThread takeRenderThreadLocked() {
        RenderThread thread = renderThread;
        renderThread = null;
        if (thread != null) {
            thread.shutdown();
        }
        return thread;
    }

    private void joinRenderThread(RenderThread thread) {
        if (thread != null && Thread.currentThread() != thread) {
            try {
                thread.join(1200L);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private void updateStatus(OfficialLive2DRendererStatus status) {
        synchronized (lock) {
            lastStatus = status;
        }
        dispatchStatus(status);
    }

    private void dispatchStatus(final OfficialLive2DRendererStatus status) {
        final Listener target;
        synchronized (lock) {
            target = listener;
        }
        if (target == null || status == null) {
            return;
        }
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                target.onStatus(status);
            }
        });
    }

    private void dispatchError(final String error) {
        final Listener target;
        synchronized (lock) {
            target = listener;
        }
        if (target == null) {
            return;
        }
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                target.onError(error);
            }
        });
    }

    private final class EngineCallback implements OfficialLive2DRenderer.Callback {
        @Override
        public void onStatus(OfficialLive2DRendererStatus status) {
            updateStatus(status);
        }

        @Override
        public void onError(String error) {
            dispatchError(error);
        }
    }

    private static final class RenderThread extends Thread {
        private static final long IDLE_FRAME_MS = 34L;
        private static final long FAST_FRAME_MS = 17L;
        private static final long PAUSED_FRAME_MS = 160L;

        private final Context appContext;
        private final SurfaceTexture surfaceTexture;
        private final OfficialLive2DRenderer renderer;
        private final OfficialLive2DRenderer.Callback callback;
        private final Object frameLock = new Object();
        private volatile boolean running = true;
        private volatile boolean paused;
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
            boolean engineReused,
            OfficialLive2DRenderer.Callback callback
        ) {
            super("PersistentLive2DTexture");
            appContext = context.getApplicationContext();
            this.surfaceTexture = surfaceTexture;
            this.width = width;
            this.height = height;
            this.callback = callback;
            renderer = new OfficialLive2DRenderer(appContext, callback);
            renderer.setEngineState(true, engineReused);
        }

        boolean uses(SurfaceTexture texture) {
            return surfaceTexture == texture;
        }

        void submitCommand(Live2DRenderCommand command) {
            renderer.submitCommand(command);
            fastFrameMode = command.getSpeaking() || command.getMouthOpen() > 0.02f;
            requestFrame();
        }

        void updateSize(int width, int height) {
            this.width = Math.max(width, 1);
            this.height = Math.max(height, 1);
            requestFrame();
        }

        void setPaused(boolean paused) {
            this.paused = paused;
            requestFrame();
        }

        void shutdown() {
            running = false;
            requestFrame();
        }

        @Override
        public void run() {
            try {
                Log.i(TAG, "render thread start surface=" + System.identityHashCode(surfaceTexture) + " size=" + width + "x" + height);
                initEgl();
                renderer.onSurfaceCreated(null, null);
                appliedWidth = width;
                appliedHeight = height;
                renderer.onSurfaceChanged(null, appliedWidth, appliedHeight);

                while (running) {
                    if (paused) {
                        waitForNextFrame(PAUSED_FRAME_MS);
                        continue;
                    }
                    if (appliedWidth != width || appliedHeight != height) {
                        appliedWidth = width;
                        appliedHeight = height;
                        renderer.onSurfaceChanged(null, appliedWidth, appliedHeight);
                    }
                    renderer.onDrawFrame(null);
                    EGL14.eglSwapBuffers(eglDisplay, eglSurface);
                    waitForNextFrame(fastFrameMode ? FAST_FRAME_MS : IDLE_FRAME_MS);
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } catch (Throwable t) {
                if (callback != null) {
                    callback.onError("Persistent Live2D EGL failed: " + t.getMessage());
                }
            } finally {
                renderer.release();
                releaseEgl();
                Log.i(TAG, "render thread stopped surface=" + System.identityHashCode(surfaceTexture));
            }
        }

        private void requestFrame() {
            synchronized (frameLock) {
                frameLock.notifyAll();
            }
        }

        private void waitForNextFrame(long frameMs) throws InterruptedException {
            synchronized (frameLock) {
                frameLock.wait(frameMs);
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
