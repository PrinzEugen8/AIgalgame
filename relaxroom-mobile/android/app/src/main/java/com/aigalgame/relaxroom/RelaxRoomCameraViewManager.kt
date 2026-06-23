package com.aigalgame.relaxroom

import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.common.MapBuilder
import com.facebook.react.uimanager.SimpleViewManager
import com.facebook.react.uimanager.ThemedReactContext
import com.facebook.react.uimanager.annotations.ReactProp

class RelaxRoomCameraViewManager : SimpleViewManager<RelaxRoomCameraPreviewView>() {
  override fun getName(): String = "RelaxRoomCameraPreview"

  override fun createViewInstance(
      reactContext: ThemedReactContext
  ): RelaxRoomCameraPreviewView = RelaxRoomCameraPreviewView(reactContext)

  @ReactProp(name = "active", defaultBoolean = false)
  fun setActive(view: RelaxRoomCameraPreviewView, active: Boolean) {
    view.setActive(active)
  }

  @ReactProp(name = "cameraFacing")
  fun setCameraFacing(view: RelaxRoomCameraPreviewView, cameraFacing: String?) {
    view.setCameraFacing(cameraFacing)
  }

  @ReactProp(name = "cameraEnabled", defaultBoolean = true)
  fun setCameraEnabled(view: RelaxRoomCameraPreviewView, cameraEnabled: Boolean) {
    view.setCameraEnabled(cameraEnabled)
  }

  @ReactProp(name = "frameIntervalMs", defaultInt = 0)
  fun setFrameIntervalMs(view: RelaxRoomCameraPreviewView, frameIntervalMs: Int) {
    view.setFrameIntervalMs(frameIntervalMs)
  }

  @ReactProp(name = "frameJpegQuality", defaultInt = 72)
  fun setFrameJpegQuality(view: RelaxRoomCameraPreviewView, frameJpegQuality: Int) {
    view.setFrameJpegQuality(frameJpegQuality)
  }

  @ReactProp(name = "frameMaxLongEdge", defaultInt = 640)
  fun setFrameMaxLongEdge(view: RelaxRoomCameraPreviewView, frameMaxLongEdge: Int) {
    view.setFrameMaxLongEdge(frameMaxLongEdge)
  }

  override fun getExportedCustomDirectEventTypeConstants(): MutableMap<String, Any> =
      MapBuilder.builder<String, Any>()
          .put("topFrame", MapBuilder.of("registrationName", "onFrame"))
          .put("topCameraError", MapBuilder.of("registrationName", "onCameraError"))
          .build()
}
