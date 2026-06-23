package com.aigalgame.relaxroom

import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.uimanager.ViewManager

class RelaxRoomCameraPackage : ReactPackage {
  override fun createNativeModules(
      reactContext: ReactApplicationContext
  ): List<NativeModule> =
      listOf(RelaxRoomAudioCaptureModule(reactContext), RelaxRoomAudioOutputModule(reactContext))

  override fun createViewManagers(
      reactContext: ReactApplicationContext
  ): List<ViewManager<*, *>> = listOf(RelaxRoomCameraViewManager())
}
