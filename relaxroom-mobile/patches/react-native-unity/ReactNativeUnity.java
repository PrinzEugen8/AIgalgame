package com.azesmwayreactnativeunity;



import android.app.Activity;

import android.graphics.PixelFormat;

import android.os.Build;

import android.os.Handler;

import android.os.Looper;

import android.util.Log;

import android.view.View;

import android.view.ViewGroup;

import android.view.WindowManager;



import static android.view.ViewGroup.LayoutParams.MATCH_PARENT;



import java.lang.reflect.InvocationTargetException;



public class ReactNativeUnity {

    private static final String TAG = "ReactNativeUnity";

    private static UPlayer unityPlayer;

    private static boolean creatingPlayer;

    private static UnityPlayerCallback pendingCreateCallback;

    private static boolean _hasUnityRuntimeResumed;

    public static boolean _isUnityReady;

    public static boolean _isUnityPaused;

    public static boolean _fullScreen;



    public static UPlayer getPlayer() {

        if (!_isUnityReady) {

            return null;

        }

        return unityPlayer;

    }



    public static boolean isUnityReady() {

        return _isUnityReady;

    }



    public static boolean isUnityPaused() {

        return _isUnityPaused;

    }



    private static boolean isUnityViewAttached() {

        if (unityPlayer == null) {

            return false;

        }



        try {

            View frame = unityPlayer.requestFrame();

            return frame.getParent() instanceof ViewGroup;

        } catch (NoSuchMethodException e) {

            return false;

        }

    }



    private static void resumeUnityRuntimeEarly(UPlayer player, String source) {

        if (player == null || _hasUnityRuntimeResumed) {

            return;

        }



        player.resume();

        _hasUnityRuntimeResumed = true;

        _isUnityPaused = false;

        RelaxRoomStartupNativeLog.mark("native_first_resume", source);

    }



    private static void resumePlayerIfAttached(UPlayer player, String source) {

        if (player == null) {

            return;

        }



        if (_hasUnityRuntimeResumed) {

            RelaxRoomStartupNativeLog.mark("native_player_resumed", "already_resumed");

            return;

        }



        if (!isUnityViewAttached()) {

            resumeUnityRuntimeEarly(player, source + "_prewarm");

            return;

        }



        resumeUnityRuntimeEarly(player, source);

        RelaxRoomStartupNativeLog.mark("native_player_resumed", source);

    }



    public static synchronized void createPlayer(final Activity activity, final UnityPlayerCallback callback) throws InvocationTargetException, NoSuchMethodException, IllegalAccessException {

        if (unityPlayer != null) {

            callback.onReady();

            return;

        }



        if (creatingPlayer) {

            Log.d(TAG, "Unity player creation already in progress");

            pendingCreateCallback = callback;

            return;

        }



        if (activity != null) {

            RelaxRoomStartupNativeLog.beginNativeTimingIfNeeded();

            creatingPlayer = true;

            activity.runOnUiThread(new Runnable() {

                @Override

                public void run() {

                    RelaxRoomStartupNativeLog.mark("native_create_player_ui_begin");

                    activity.getWindow().setFormat(PixelFormat.RGBA_8888);

                    int flag = activity.getWindow().getAttributes().flags;

                    final boolean fullScreen = (flag & WindowManager.LayoutParams.FLAG_FULLSCREEN) == WindowManager.LayoutParams.FLAG_FULLSCREEN;



                    try {

                        RelaxRoomStartupNativeLog.mark("native_player_ctor_invoke");

                        unityPlayer = new UPlayer(activity, callback);

                    } catch (ClassNotFoundException | InstantiationException | IllegalAccessException | InvocationTargetException e) {

                        Log.e(TAG, "Failed to create Unity player", e);

                        creatingPlayer = false;

                        return;

                    }



                    if (unityPlayer == null) {

                        Log.e(TAG, "Unity player is null after creation");

                        creatingPlayer = false;

                        return;

                    }



                    unityPlayer.configureSurfaceViewZOrderForOverlay();

                    RelaxRoomStartupNativeLog.mark("native_finish_setup_scheduled", "delay_ms=50");

                    final Runnable finishPlayerSetup = new Runnable() {

                        @Override

                        public void run() {

                            RelaxRoomStartupNativeLog.mark("native_finish_setup_begin");

                            unityPlayer.windowFocusChanged(true);



                            try {

                                unityPlayer.requestFocusPlayer();

                            } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {

                                Log.e(TAG, "Failed to focus Unity player", e);

                            }



                            resumePlayerIfAttached(unityPlayer, "prewarm");



                            if (!_fullScreen || !fullScreen) {

                                activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_FORCE_NOT_FULLSCREEN);

                                activity.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);

                            }



                            _isUnityReady = true;

                            creatingPlayer = false;

                            RelaxRoomStartupNativeLog.mark("native_player_ready");
                            RelaxRoomStartupNativeLog.mark("native_finish_setup_end");



                            try {

                                callback.onReady();

                            } catch (InvocationTargetException | IllegalAccessException | NoSuchMethodException e) {

                                Log.e(TAG, "Unity ready callback failed", e);

                            }



                            notifyPendingCreateCallback();

                        }

                    };



                    new Handler(Looper.getMainLooper()).postDelayed(finishPlayerSetup, 50);

                }

            });

        }

    }



    private static void notifyPendingCreateCallback() {

        if (pendingCreateCallback == null) {

            return;

        }



        UnityPlayerCallback pending = pendingCreateCallback;

        pendingCreateCallback = null;



        try {

            pending.onReady();

        } catch (InvocationTargetException | IllegalAccessException | NoSuchMethodException e) {

            Log.e(TAG, "Pending Unity ready callback failed", e);

        }

    }



    public static void pause() {

        if (unityPlayer != null) {

            unityPlayer.pause();

            _isUnityPaused = true;

        }

    }



    public static void resume() {

        if (unityPlayer != null) {

            resumeUnityRuntimeEarly(unityPlayer, "manual");

            RelaxRoomStartupNativeLog.mark("native_player_resumed", "manual");

        }

    }



    public static void unload() {

        if (unityPlayer != null) {

            unityPlayer.unload();

            _isUnityPaused = false;

            _hasUnityRuntimeResumed = false;

        }

    }



    public static void addUnityViewToBackground() throws InvocationTargetException, NoSuchMethodException, IllegalAccessException {

        if (unityPlayer == null) {

            return;

        }



        if (unityPlayer.getParentPlayer() != null) {

            ((ViewGroup) unityPlayer.getParentPlayer()).endViewTransition(unityPlayer.requestFrame());

            ((ViewGroup) unityPlayer.getParentPlayer()).removeView(unityPlayer.requestFrame());

        }



        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {

            unityPlayer.setZ(-1f);

        }



        final Activity activity = ((Activity) unityPlayer.getContextPlayer());

        unityPlayer.configureSurfaceViewZOrderForOverlay();

        ViewGroup.LayoutParams layoutParams = new ViewGroup.LayoutParams(1, 1);

        activity.addContentView(unityPlayer.requestFrame(), layoutParams);

    }



    public static void addUnityViewToGroup(ViewGroup group) throws NoSuchMethodException, InvocationTargetException, IllegalAccessException {

        if (unityPlayer == null) {

            return;

        }

        View unityFrame = unityPlayer.requestFrame();
        Object currentParent = unityPlayer.getParentPlayer();
        if (currentParent == group) {
            RelaxRoomStartupNativeLog.mark("native_view_attached", "already_attached");
            resumePlayerIfAttached(unityPlayer, "attach");
            return;
        }

        if (currentParent != null) {

            ((ViewGroup) currentParent).removeView(unityFrame);

        }

        ViewGroup.LayoutParams layoutParams = new ViewGroup.LayoutParams(MATCH_PARENT, MATCH_PARENT);

        group.addView(unityFrame, 0, layoutParams);

        unityPlayer.configureSurfaceViewZOrderForOverlay();

        RelaxRoomStartupNativeLog.mark("native_view_attached");



        final UPlayer player = unityPlayer;

        final Runnable attachResume = new Runnable() {

            @Override

            public void run() {

                try {

                    player.windowFocusChanged(true);

                    player.requestFocusPlayer();

                    resumePlayerIfAttached(player, "attach");

                } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {

                    Log.e(TAG, "Failed to resume Unity after attach", e);

                }

            }

        };

        new Handler(Looper.getMainLooper()).post(attachResume);

    }



    public interface UnityPlayerCallback {

        void onReady() throws InvocationTargetException, NoSuchMethodException, IllegalAccessException;

        void onUnload();

        void onQuit();
    }
}

