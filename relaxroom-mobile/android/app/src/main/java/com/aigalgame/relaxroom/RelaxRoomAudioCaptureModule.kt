package com.aigalgame.relaxroom

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Base64
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.modules.core.DeviceEventManagerModule
import kotlin.math.max

class RelaxRoomAudioCaptureModule(
    private val reactContext: ReactApplicationContext
) : ReactContextBaseJavaModule(reactContext) {
  private val lock = Any()
  @Volatile private var running = false
  private var audioRecord: AudioRecord? = null
  private var audioThread: Thread? = null

  override fun getName(): String = "RelaxRoomAudioCapture"

  @ReactMethod
  fun start(sampleRate: Int, chunkMs: Int, promise: Promise) {
    synchronized(lock) {
      if (running) {
        promise.resolve(null)
        return
      }

      if (
          ContextCompat.checkSelfPermission(reactContext, Manifest.permission.RECORD_AUDIO) !=
              PackageManager.PERMISSION_GRANTED
      ) {
        promise.reject("microphone_permission_missing", "Microphone permission is not granted")
        return
      }

      val safeSampleRate = sampleRate.coerceIn(8000, 48000)
      val safeChunkMs = chunkMs.coerceIn(20, 200)
      val channel = AudioFormat.CHANNEL_IN_MONO
      val encoding = AudioFormat.ENCODING_PCM_16BIT
      val minBufferSize = AudioRecord.getMinBufferSize(safeSampleRate, channel, encoding)
      if (minBufferSize <= 0) {
        promise.reject("audio_record_unavailable", "AudioRecord min buffer is unavailable")
        return
      }

      val chunkBytes = max(320, safeSampleRate * 2 * safeChunkMs / 1000)
      val bufferSize = max(minBufferSize * 2, chunkBytes * 4)
      val record =
          AudioRecord(
              MediaRecorder.AudioSource.VOICE_COMMUNICATION,
              safeSampleRate,
              channel,
              encoding,
              bufferSize,
          )

      if (record.state != AudioRecord.STATE_INITIALIZED) {
        record.release()
        promise.reject("audio_record_init_failed", "AudioRecord failed to initialize")
        return
      }

      audioRecord = record
      running = true
      audioThread =
          Thread(
              {
                runCaptureLoop(record, chunkBytes, safeSampleRate)
              },
              "RelaxRoomAudioCapture",
          )
      audioThread?.start()
      promise.resolve(null)
    }
  }

  @ReactMethod
  fun stop(promise: Promise) {
    stopCapture()
    promise.resolve(null)
  }

  @ReactMethod
  fun addListener(eventName: String) {
    // Required by NativeEventEmitter.
  }

  @ReactMethod
  fun removeListeners(count: Int) {
    // Required by NativeEventEmitter.
  }

  override fun invalidate() {
    stopCapture()
    super.invalidate()
  }

  private fun runCaptureLoop(record: AudioRecord, chunkBytes: Int, sampleRate: Int) {
    val buffer = ByteArray(chunkBytes)
    try {
      record.startRecording()
      emitState("recording")
      while (running) {
        val read = record.read(buffer, 0, buffer.size)
        if (read > 0) {
          emitFrame(buffer, read, sampleRate)
        } else if (read < 0) {
          emitError("AudioRecord read failed: $read")
          break
        }
      }
    } catch (error: Exception) {
      emitError(error.message ?: "Audio capture failed")
    } finally {
      try {
        record.stop()
      } catch (_: Exception) {
      }
      record.release()
      synchronized(lock) {
        if (audioRecord === record) {
          audioRecord = null
        }
        running = false
      }
      emitState("stopped")
    }
  }

  private fun stopCapture() {
    val thread: Thread?
    synchronized(lock) {
      running = false
      thread = audioThread
      audioThread = null
    }

    try {
      audioRecord?.stop()
    } catch (_: Exception) {
    }

    if (thread != null && thread !== Thread.currentThread()) {
      try {
        thread.join(500)
      } catch (_: InterruptedException) {
        Thread.currentThread().interrupt()
      }
    }
  }

  private fun emitFrame(buffer: ByteArray, read: Int, sampleRate: Int) {
    val bytes = if (read == buffer.size) buffer else buffer.copyOf(read)
    val event =
        Arguments.createMap().apply {
          putString("audio", Base64.encodeToString(bytes, Base64.NO_WRAP))
          putInt("sampleRate", sampleRate)
          putInt("bytes", read)
        }
    emit("RelaxRoomAudioFrame", event)
  }

  private fun emitState(state: String) {
    val event = Arguments.createMap().apply { putString("state", state) }
    emit("RelaxRoomAudioState", event)
  }

  private fun emitError(message: String) {
    val event = Arguments.createMap().apply { putString("message", message) }
    emit("RelaxRoomAudioError", event)
  }

  private fun emit(eventName: String, event: Any) {
    if (!reactContext.hasActiveReactInstance()) {
      return
    }

    reactContext
        .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
        .emit(eventName, event)
  }
}
