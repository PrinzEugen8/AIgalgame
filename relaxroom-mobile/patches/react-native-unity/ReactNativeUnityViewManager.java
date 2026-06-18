package com.azesmwayreactnativeunity;

import static com.azesmwayreactnativeunity.ReactNativeUnity.*;

import android.os.Handler;
import android.view.View;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.facebook.infer.annotation.Assertions;
import com.facebook.react.bridge.Arguments;
import com.facebook.react.bridge.LifecycleEventListener;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.bridge.ReactContext;
import com.facebook.react.bridge.ReadableArray;
import com.facebook.react.bridge.WritableMap;
import com.facebook.react.common.MapBuilder;
import com.facebook.react.module.annotations.ReactModule;
import com.facebook.react.uimanager.ThemedReactContext;
import com.facebook.react.uimanager.annotations.ReactProp;
import com.facebook.react.uimanager.events.RCTEventEmitter;

import java.lang.reflect.InvocationTargetException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@ReactModule(name = ReactNativeUnityViewManager.NAME)
public class ReactNativeUnityViewManager extends ReactNativeUnityViewManagerSpec<ReactNativeUnityView> implements LifecycleEventListener, View.OnAttachStateChangeListener {
  ReactApplicationContext context;
  static ReactNativeUnityView view;
  static final List<String> pendingMessages = new ArrayList<>();
  public static final String NAME = "RNUnityView";

  public ReactNativeUnityViewManager(ReactApplicationContext context) {
    super();
    this.context = context;
    context.addLifecycleEventListener(this);
  }

  @NonNull
  @Override
  public String getName() {
    return NAME;
  }

  @NonNull
  @Override
  public ReactNativeUnityView createViewInstance(@NonNull ThemedReactContext context) {
    RelaxRoomStartupNativeLog.beginNativeTimingIfNeeded();
    view = new ReactNativeUnityView(this.context);
    view.addOnAttachStateChangeListener(this);
    RelaxRoomStartupNativeLog.mark("native_view_create");

    if (getPlayer() != null) {
        try {
            view.setUnityPlayer(getPlayer());
            flushPendingMessages();
        } catch (InvocationTargetException | NoSuchMethodException | IllegalAccessException e) {}
    } else {
        try {
            createPlayer(context.getCurrentActivity(), new UnityPlayerCallback() {
              @Override
              public void onReady() throws InvocationTargetException, NoSuchMethodException, IllegalAccessException {
                view.setUnityPlayer(getPlayer());
                flushPendingMessages();
              }

              @Override
              public void onUnload() {
                WritableMap data = Arguments.createMap();
                data.putString("message", "MyMessage");
                ReactContext reactContext = (ReactContext) view.getContext();
                reactContext.getJSModule(RCTEventEmitter.class).receiveEvent(view.getId(), "onPlayerUnload", data);
              }

              @Override
              public void onQuit() {
                WritableMap data = Arguments.createMap();
                data.putString("message", "MyMessage");
                ReactContext reactContext = (ReactContext) view.getContext();
                reactContext.getJSModule(RCTEventEmitter.class).receiveEvent(view.getId(), "onPlayerQuit", data);
              }
            });
        } catch (InvocationTargetException | NoSuchMethodException | IllegalAccessException e) {}
    }

    return view;
  }

  @Override
  public Map<String, Object> getExportedCustomDirectEventTypeConstants() {
    Map<String, Object> export = super.getExportedCustomDirectEventTypeConstants();

    if (export == null) {
      export = MapBuilder.newHashMap();
    }

    export.put("onUnityMessage", MapBuilder.of("registrationName", "onUnityMessage"));
    export.put("onPlayerUnload", MapBuilder.of("registrationName", "onPlayerUnload"));
    export.put("onPlayerQuit", MapBuilder.of("registrationName", "onPlayerQuit"));

    return export;
  }

  @Override
  public void receiveCommand(@NonNull ReactNativeUnityView view, String commandType, @Nullable ReadableArray args) {
    Assertions.assertNotNull(view);
    Assertions.assertNotNull(args);

    switch (commandType) {
      case "postMessage":
        assert args != null;
        postMessage(view, args.getString(0), args.getString(1), args.getString(2));
        return;
      case "unloadUnity":
        unloadUnity(view);
        return;
      case "pauseUnity":
        assert args != null;
        pauseUnity(view, args.getBoolean(0));
        return;
      case "resumeUnity":
        resumeUnity(view);
        return;
      case "windowFocusChanged":
        assert args != null;
        windowFocusChanged(view, args.getBoolean(0));
        return;
      default:
        throw new IllegalArgumentException(String.format(
          "Unsupported command %s received by %s.",
          commandType,
          getClass().getSimpleName()));
    }
  }

  @Override
  public void unloadUnity(ReactNativeUnityView view) {
    if (isUnityReady()) {
      unload();
    }
  }

  @Override
  public void pauseUnity(ReactNativeUnityView view, boolean pause) {
    if (!isUnityReady()) {
      return;
    }

    assert getPlayer() != null;
    if (pause) {
      getPlayer().pause();
      _isUnityPaused = true;
    } else {
      getPlayer().resume();
      _isUnityPaused = false;
    }
  }

  @Override
  public void resumeUnity(ReactNativeUnityView view) {
    if (isUnityReady()) {
      assert getPlayer() != null;
      getPlayer().resume();
      _isUnityPaused = false;
    }
  }

  @Override
  public void windowFocusChanged(ReactNativeUnityView view, boolean hasFocus) {
    if (isUnityReady()) {
      assert getPlayer() != null;
      getPlayer().windowFocusChanged(hasFocus);
    }
  }

  public static void sendMessageToMobileApp(String message) {
    if (message != null && message.contains("\"evt\":\"startup_milestone\"")) {
      String stage = extractJsonStringField(message, "stage");
      String detail = extractJsonStringField(message, "detail");
      if (stage != null) {
        RelaxRoomStartupNativeLog.onUnityStageReceived(stage, detail);
      }
    } else if (message != null && message.contains("\"evt\":\"startup_timeline\"")) {
      RelaxRoomStartupNativeLog.onUnityStageReceived("startup_timeline");
    }

    if (view == null) {
      pendingMessages.add(message);
      return;
    }

    dispatchMessage(message);
  }

  private static void dispatchMessage(String message) {
    if (view == null) {
      pendingMessages.add(message);
      return;
    }

    WritableMap data = Arguments.createMap();
    data.putString("message", message);
    ReactContext reactContext = (ReactContext) view.getContext();
    reactContext.getJSModule(RCTEventEmitter.class).receiveEvent(view.getId(), "onUnityMessage", data);
  }

  private static String extractJsonStringField(String json, String fieldName) {
    if (json == null || fieldName == null) {
      return null;
    }

    String token = "\"" + fieldName + "\":\"";
    int start = json.indexOf(token);
    if (start < 0) {
      return null;
    }

    start += token.length();
    int end = start;
    while (end < json.length()) {
      if (json.charAt(end) == '\\') {
        end += 2;
        continue;
      }
      if (json.charAt(end) == '"') {
        break;
      }
      end += 1;
    }

    if (end >= json.length()) {
      return null;
    }

    return json.substring(start, end)
      .replace("\\\"", "\"")
      .replace("\\\\", "\\");
  }

  private static void flushPendingMessages() {
    if (view == null || pendingMessages.isEmpty()) {
      return;
    }

    int pendingCount = pendingMessages.size();
    List<String> copy = new ArrayList<>(pendingMessages);
    pendingMessages.clear();
    for (String message : copy) {
      dispatchMessage(message);
    }
    RelaxRoomStartupNativeLog.mark("native_pending_flushed", "count=" + pendingCount);
  }

  @Override
  public void onDropViewInstance(ReactNativeUnityView view) {
    view.removeOnAttachStateChangeListener(this);
    super.onDropViewInstance(view);
  }

  @Override
  public void onHostResume() {
    if (isUnityReady()) {
      assert getPlayer() != null;
      getPlayer().resume();
      restoreUnityUserState();
    }
  }

  @Override
  public void onHostPause() {
    if (isUnityReady()) {
      assert getPlayer() != null;
      getPlayer().pause();
    }
  }

  @Override
  public void onHostDestroy() {
    if (isUnityReady()) {
      assert getPlayer() != null;
      getPlayer().destroy();
    }
  }

  private void restoreUnityUserState() {
    if (isUnityPaused()) {
      Handler handler = new Handler();
      handler.postDelayed(new Runnable() {
        @Override
        public void run() {
          if (getPlayer() != null) {
            getPlayer().pause();
          }
        }
      }, 300);
    }
  }

  @Override
  public void onViewAttachedToWindow(View v) {
    RelaxRoomStartupNativeLog.mark("native_view_window_attached");
    flushPendingMessages();
  }

  @Override
  public void onViewDetachedFromWindow(View v) {}

  @ReactProp(name = "androidKeepPlayerMounted", defaultBoolean = false)
  public void setAndroidKeepPlayerMounted(ReactNativeUnityView view, boolean keepPlayerMounted) {
    view.keepPlayerMounted = keepPlayerMounted;
  }

  @ReactProp(name = "fullScreen", defaultBoolean = true)
  public void setFullScreen(ReactNativeUnityView view, boolean fullScreen) {
    _fullScreen = fullScreen;
  }

  @Override
  public void postMessage(ReactNativeUnityView view, String gameObject, String methodName, String message) {
    if (isUnityReady()) {
      assert getPlayer() != null;
      UPlayer.UnitySendMessage(gameObject, methodName, message);
    }
  }
}
