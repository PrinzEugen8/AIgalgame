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



    private static void resumePlayerIfAttached(UPlayer player) {

        if (!isUnityViewAttached()) {

            Log.d(TAG, "Defer Unity resume until view is attached to ReactNativeUnityView");

            return;

        }



        player.resume();

    }



    public static synchronized void createPlayer(final Activity activity, final UnityPlayerCallback callback) throws InvocationTargetException, NoSuchMethodException, IllegalAccessException {

        if (unityPlayer != null) {

            callback.onReady();

            return;

        }



        if (creatingPlayer) {

            Log.d(TAG, "Unity player creation already in progress");

            return;

        }



        if (activity != null) {

            RelaxRoomStartupNativeLog.beginNativeTiming();

            creatingPlayer = true;

            activity.runOnUiThread(new Runnable() {

                @Override

                public void run() {

                    activity.getWindow().setFormat(PixelFormat.RGBA_8888);

                    int flag = activity.getWindow().getAttributes().flags;

                    final boolean fullScreen = (flag & WindowManager.LayoutParams.FLAG_FULLSCREEN) == WindowManager.LayoutParams.FLAG_FULLSCREEN;



                    try {

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



                    final Runnable finishPlayerSetup = new Runnable() {

                        @Override

                        public void run() {

                            unityPlayer.windowFocusChanged(true);



                            try {

                                unityPlayer.requestFocusPlayer();

                            } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {

                                Log.e(TAG, "Failed to focus Unity player", e);

                            }



                            resumePlayerIfAttached(unityPlayer);



                            if (!_fullScreen || !fullScreen) {

                                activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_FORCE_NOT_FULLSCREEN);

                                activity.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);

                            }



                            _isUnityReady = true;

                            creatingPlayer = false;

                            RelaxRoomStartupNativeLog.mark("native_player_ready");



                            try {

                                callback.onReady();

                            } catch (InvocationTargetException | IllegalAccessException | NoSuchMethodException e) {

                                Log.e(TAG, "Unity ready callback failed", e);

                            }

                        }

                    };



                    new Handler(Looper.getMainLooper()).postDelayed(finishPlayerSetup, 50);

                }

            });

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

            unityPlayer.resume();

            _isUnityPaused = false;

        }

    }



    public static void unload() {

        if (unityPlayer != null) {

            unityPlayer.unload();

            _isUnityPaused = false;

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



        if (unityPlayer.getParentPlayer() != null) {

            ((ViewGroup) unityPlayer.getParentPlayer()).removeView(unityPlayer.requestFrame());

        }



        View unityFrame = unityPlayer.requestFrame();

        unityPlayer.configureSurfaceViewZOrderForOverlay();

        ViewGroup.LayoutParams layoutParams = new ViewGroup.LayoutParams(MATCH_PARENT, MATCH_PARENT);

        group.addView(unityFrame, 0, layoutParams);

        unityPlayer.configureSurfaceViewZOrderForOverlay();

        RelaxRoomStartupNativeLog.mark("native_view_attached");



        final UPlayer player = unityPlayer;

        new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {

            @Override

            public void run() {

                try {

                    player.windowFocusChanged(true);

                    player.requestFocusPlayer();

                    player.resume();

                    player.configureSurfaceViewZOrderForOverlay();

                    RelaxRoomStartupNativeLog.mark("native_player_resumed");

                } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {

                    Log.e(TAG, "Failed to resume Unity after attach", e);

                }

            }

        }, 100);

    }



    public interface UnityPlayerCallback {

        void onReady() throws InvocationTargetException, NoSuchMethodException, IllegalAccessException;

        void onUnload();

        void onQuit();
    }
}


