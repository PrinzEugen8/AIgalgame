package com.aigalgame.relaxroom

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Base64
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import java.util.concurrent.Executors
import kotlin.math.max

class RelaxRoomAudioOutputModule(
    private val reactContext: ReactApplicationContext
) : ReactContextBaseJavaModule(reactContext) {
  private val lock = Any()
  private val playbackExecutor = Executors.newSingleThreadExecutor()
  private var audioTrack: AudioTrack? = null
  private var audioManager: AudioManager? = null
  private var previousMode: Int? = null
  private var previousSpeakerphoneOn: Boolean? = null
  @Volatile private var playbackSession = 0
  private var outputSampleRate = 24000

  override fun getName(): String = "RelaxRoomAudioOutput"

  @ReactMethod
  fun start(sampleRate: Int, promise: Promise) {
    try {
      synchronized(lock) {
        outputSampleRate = sampleRate.coerceIn(8000, 48000)
        configureAudioRouteLocked()
        ensureAudioTrackLocked()
      }
      promise.resolve(null)
    } catch (error: Exception) {
      promise.reject("audio_output_start_failed", error.message ?: "Audio output failed to start")
    }
  }

  @ReactMethod
  fun playPcm16Base64(audio: String, promise: Promise) {
    val bytes =
        try {
          Base64.decode(audio, Base64.DEFAULT)
        } catch (error: Exception) {
          promise.reject("audio_output_decode_failed", error.message ?: "Audio output decode failed")
          return
        }

    if (bytes.isEmpty()) {
      promise.resolve(null)
      return
    }

    val session = playbackSession
    playbackExecutor.execute {
      if (session != playbackSession) {
        return@execute
      }
      try {
        synchronized(lock) {
          if (session != playbackSession) {
            return@synchronized
          }
          configureAudioRouteLocked()
          val track = ensureAudioTrackLocked()
          track.write(bytes, 0, bytes.size)
        }
      } catch (_: Exception) {
      }
    }
    promise.resolve(null)
  }

  @ReactMethod
  fun stop(promise: Promise) {
    stopPlayback()
    promise.resolve(null)
  }

  @ReactMethod
  fun addListener(eventName: String) {
    // Required by React Native when a NativeEventEmitter is ever attached.
  }

  @ReactMethod
  fun removeListeners(count: Int) {
    // Required by React Native when a NativeEventEmitter is ever attached.
  }

  override fun invalidate() {
    stopPlayback()
    playbackExecutor.shutdownNow()
    super.invalidate()
  }

  private fun ensureAudioTrackLocked(): AudioTrack {
    val existing = audioTrack
    if (existing != null && existing.state == AudioTrack.STATE_INITIALIZED) {
      if (existing.playState != AudioTrack.PLAYSTATE_PLAYING) {
        existing.play()
      }
      return existing
    }

    existing?.release()

    val channel = AudioFormat.CHANNEL_OUT_MONO
    val encoding = AudioFormat.ENCODING_PCM_16BIT
    val minBufferSize = AudioTrack.getMinBufferSize(outputSampleRate, channel, encoding)
    val bufferSize = max(minBufferSize * 2, outputSampleRate * 2)
    val track =
        AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(outputSampleRate)
                    .setEncoding(encoding)
                    .setChannelMask(channel)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(bufferSize)
            .build()

    if (track.state != AudioTrack.STATE_INITIALIZED) {
      track.release()
      throw IllegalStateException("AudioTrack failed to initialize")
    }

    track.play()
    audioTrack = track
    return track
  }

  private fun configureAudioRouteLocked() {
    val manager =
        audioManager
            ?: (reactContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager).also {
              audioManager = it
            }

    if (previousMode == null) {
      previousMode = manager.mode
    }
    if (previousSpeakerphoneOn == null) {
      previousSpeakerphoneOn = manager.isSpeakerphoneOn
    }

    manager.mode = AudioManager.MODE_IN_COMMUNICATION
    manager.isSpeakerphoneOn = true
  }

  private fun restoreAudioRouteLocked() {
    val manager = audioManager ?: return
    val mode = previousMode
    val speakerphoneOn = previousSpeakerphoneOn
    if (mode != null) {
      manager.mode = mode
    }
    if (speakerphoneOn != null) {
      manager.isSpeakerphoneOn = speakerphoneOn
    }
    previousMode = null
    previousSpeakerphoneOn = null
  }

  private fun stopPlayback() {
    playbackSession += 1
    synchronized(lock) {
      val track = audioTrack
      audioTrack = null
      if (track != null) {
        try {
          track.pause()
          track.flush()
        } catch (_: Exception) {
        }
        try {
          track.stop()
        } catch (_: Exception) {
        }
        track.release()
      }
      restoreAudioRouteLocked()
    }
  }
}
