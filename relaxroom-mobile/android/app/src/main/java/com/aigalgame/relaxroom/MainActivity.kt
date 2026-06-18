package com.aigalgame.relaxroom

import android.os.Bundle
import android.util.Log
import com.azesmwayreactnativeunity.ReactNativeUnity
import com.facebook.react.ReactActivity
import com.facebook.react.ReactActivityDelegate
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.fabricEnabled
import com.facebook.react.defaults.DefaultReactActivityDelegate

class MainActivity : ReactActivity() {
  override fun getMainComponentName(): String = "RelaxRoomMobile"

  override fun createReactActivityDelegate(): ReactActivityDelegate =
      DefaultReactActivityDelegate(this, mainComponentName, fabricEnabled)

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    prewarmUnityPlayer()
  }

  private fun prewarmUnityPlayer() {
    try {
      ReactNativeUnity.createPlayer(
          this,
          object : ReactNativeUnity.UnityPlayerCallback {
            override fun onReady() {
              Log.i("RelaxRoom", "Unity player prewarmed")
            }

            override fun onUnload() {}

            override fun onQuit() {}
          })
    } catch (error: Exception) {
      Log.w("RelaxRoom", "Unity prewarm skipped", error)
    }
  }
}
