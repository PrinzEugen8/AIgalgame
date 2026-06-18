package com.azesmwayreactnativeunity;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;

import com.unity3d.player.*;

import java.lang.reflect.Constructor;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

public class UPlayer {
    private UnityPlayer unityPlayer;

    public UPlayer(final Activity activity, final ReactNativeUnity.UnityPlayerCallback callback) throws ClassNotFoundException, InvocationTargetException, IllegalAccessException, InstantiationException {
        super();
        Class<?> playerClass;

        RelaxRoomStartupNativeLog.mark("native_player_ctor_start");

        try {
            playerClass = Class.forName("com.unity3d.player.UnityPlayerForActivityOrService");
            RelaxRoomStartupNativeLog.mark("native_unity_class_resolved", "UnityPlayerForActivityOrService");
        } catch (ClassNotFoundException e) {
            playerClass = Class.forName("com.unity3d.player.UnityPlayer");
            RelaxRoomStartupNativeLog.mark("native_unity_class_resolved", "UnityPlayer");
        }

        RelaxRoomStartupNativeLog.mark("native_unity_player_new_begin");
        unityPlayer = createUnityPlayer(playerClass, activity, callback);
        RelaxRoomStartupNativeLog.mark("native_unity_player_new_end");
        RelaxRoomStartupNativeLog.markPlayerCtorEnd(playerClass.getSimpleName());
    }

    private static UnityPlayer createUnityPlayer(
        Class<?> playerClass,
        Activity activity,
        ReactNativeUnity.UnityPlayerCallback callback
    ) throws InstantiationException, IllegalAccessException, InvocationTargetException {
        IUnityPlayerLifecycleEvents lifecycleEvents = new IUnityPlayerLifecycleEvents() {
            @Override
            public void onUnityPlayerUnloaded() {
                callback.onUnload();
            }

            @Override
            public void onUnityPlayerQuitted() {
                callback.onQuit();
            }
        };

        for (Constructor<?> constructor : playerClass.getConstructors()) {
            Class<?>[] parameterTypes = constructor.getParameterTypes();

            if (parameterTypes.length == 1 && Context.class.isAssignableFrom(parameterTypes[0])) {
                return (UnityPlayer) constructor.newInstance(activity);
            }

            if (parameterTypes.length == 2
                && Context.class.isAssignableFrom(parameterTypes[0])
                && IUnityPlayerLifecycleEvents.class.isAssignableFrom(parameterTypes[1])) {
                return (UnityPlayer) constructor.newInstance(activity, lifecycleEvents);
            }

            if (parameterTypes.length == 2
                && Activity.class.isAssignableFrom(parameterTypes[0])
                && IUnityPlayerLifecycleEvents.class.isAssignableFrom(parameterTypes[1])) {
                return (UnityPlayer) constructor.newInstance(activity, lifecycleEvents);
            }
        }

        throw new InstantiationException("No compatible Unity player constructor found for " + playerClass.getName());
    }

    public static void UnitySendMessage(String gameObject, String methodName, String message) {
        UnityPlayer.UnitySendMessage(gameObject, methodName, message);
    }

    public void pause() {
        if (unityPlayer != null) {
            unityPlayer.pause();
        }
    }

    public void windowFocusChanged(boolean b) {
        if (unityPlayer != null) {
            unityPlayer.windowFocusChanged(b);
        }
    }

    public void resume() {
        if (unityPlayer != null) {
            unityPlayer.resume();
            configureSurfaceViewZOrderForOverlay();
        }
    }

    public void unload() {
        if (unityPlayer != null) {
            unityPlayer.unload();
        }
    }

    public Object getParentPlayer() {
        try {
            View frame = requestFrame();
            if (frame.getParent() instanceof ViewGroup) {
                return frame.getParent();
            }
        } catch (NoSuchMethodException ignored) {
        }

        return null;
    }

    public void configurationChanged(Configuration newConfig) {
        if (unityPlayer != null) {
            unityPlayer.configurationChanged(newConfig);
        }
    }

    public void destroy() {
        if (unityPlayer != null) {
            unityPlayer.destroy();
        }
    }

    public void requestFocusPlayer() throws NoSuchMethodException, InvocationTargetException, IllegalAccessException {
        View frame = requestFrame();
        frame.requestFocus();
    }

    public View requestFrame() throws NoSuchMethodException {
        Exception lastError = null;

        for (String methodName : new String[]{"getFrameLayout", "getView"}) {
            try {
                Method method = unityPlayer.getClass().getMethod(methodName);
                Object result = method.invoke(unityPlayer);
                if (result instanceof View) {
                    return (View) result;
                }
            } catch (Exception error) {
                lastError = error;
            }
        }

        if (lastError != null) {
            throw new NoSuchMethodException("Unable to resolve Unity view from player: " + lastError.getMessage());
        }

        throw new NoSuchMethodException("Unable to resolve Unity view from player");
    }

    public void configureSurfaceViewZOrderForOverlay() {
        try {
            configureSurfaceViewZOrderForOverlay(requestFrame());
        } catch (NoSuchMethodException ignored) {
        }
    }

    private static void configureSurfaceViewZOrderForOverlay(View view) {
        if (view instanceof SurfaceView) {
            SurfaceView surfaceView = (SurfaceView) view;
            surfaceView.setZOrderOnTop(false);
            surfaceView.setZOrderMediaOverlay(false);
        }

        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                configureSurfaceViewZOrderForOverlay(group.getChildAt(i));
            }
        }
    }

    public void setZ(float v) throws NoSuchMethodException, InvocationTargetException, IllegalAccessException {
        try {
            Method setZ = unityPlayer.getClass().getMethod("setZ", float.class);
            setZ.invoke(unityPlayer, v);
        } catch (NoSuchMethodException ignored) {
        }
    }

    public Object getContextPlayer() {
        return unityPlayer.getContext();
    }
}
