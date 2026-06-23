package com.aigalgame.relaxroom

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.YuvImage
import android.util.Base64
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.facebook.react.bridge.Arguments
import com.facebook.react.uimanager.ThemedReactContext
import com.facebook.react.uimanager.events.RCTEventEmitter
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.max

class RelaxRoomCameraPreviewView(
    private val reactContext: ThemedReactContext
) : FrameLayout(reactContext) {
  private val cameraImageView =
      ImageView(reactContext).apply {
        scaleType = ImageView.ScaleType.CENTER_CROP
        setBackgroundColor(android.graphics.Color.BLACK)
      }

  private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
  private var cameraProvider: ProcessCameraProvider? = null
  private var active = false
  private var cameraEnabled = true
  private var cameraFacing = "front"
  private var frameIntervalMs = 0
  private var frameJpegQuality = 72
  private var frameMaxLongEdge = 640
  private var lastFrameAtMs = 0L
  private var lastPreviewAtMs = 0L
  private var bindPending = false

  init {
    addView(
        cameraImageView,
        LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT),
    )
  }

  fun setActive(nextActive: Boolean) {
    if (active == nextActive) {
      return
    }
    active = nextActive
    updateCameraBinding()
  }

  fun setCameraEnabled(nextCameraEnabled: Boolean) {
    if (cameraEnabled == nextCameraEnabled) {
      return
    }
    cameraEnabled = nextCameraEnabled
    updateCameraBinding()
  }

  fun setCameraFacing(nextCameraFacing: String?) {
    val normalized =
        if (nextCameraFacing.equals("back", ignoreCase = true)) {
          "back"
        } else {
          "front"
        }
    if (cameraFacing == normalized) {
      return
    }
    cameraFacing = normalized
    updateCameraBinding()
  }

  fun setFrameIntervalMs(nextFrameIntervalMs: Int) {
    frameIntervalMs = max(0, nextFrameIntervalMs)
    updateCameraBinding()
  }

  fun setFrameJpegQuality(nextFrameJpegQuality: Int) {
    frameJpegQuality = nextFrameJpegQuality.coerceIn(30, 95)
  }

  fun setFrameMaxLongEdge(nextFrameMaxLongEdge: Int) {
    frameMaxLongEdge = nextFrameMaxLongEdge.coerceIn(240, 1280)
    updateCameraBinding()
  }

  override fun onAttachedToWindow() {
    super.onAttachedToWindow()
    updateCameraBinding()
  }

  override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
    super.onLayout(changed, left, top, right, bottom)
    cameraImageView.layout(0, 0, right - left, bottom - top)
    if (changed) {
      updateCameraBinding()
    }
  }

  override fun onDetachedFromWindow() {
    unbindCamera()
    super.onDetachedFromWindow()
  }

  private fun updateCameraBinding() {
    if (!isAttachedToWindow || !active || !cameraEnabled) {
      cameraImageView.visibility = if (cameraEnabled) View.VISIBLE else View.INVISIBLE
      if (!cameraEnabled) {
        cameraImageView.setImageDrawable(null)
      }
      unbindCamera()
      return
    }

    cameraImageView.visibility = View.VISIBLE
    bindCamera()
  }

  private fun bindCamera() {
    if (bindPending) {
      return
    }
    if (
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) !=
            PackageManager.PERMISSION_GRANTED
    ) {
      emitError("Camera permission is not granted")
      return
    }

    val lifecycleOwner = reactContext.currentActivity as? LifecycleOwner
    if (lifecycleOwner == null) {
      emitError("Camera lifecycle owner is unavailable")
      return
    }

    bindPending = true
    val providerFuture = ProcessCameraProvider.getInstance(context)
    providerFuture.addListener(
        {
          bindPending = false
          try {
            val provider = providerFuture.get()
            cameraProvider = provider
            provider.unbindAll()

            val selector =
                if (cameraFacing == "back") {
                  CameraSelector.DEFAULT_BACK_CAMERA
                } else {
                  CameraSelector.DEFAULT_FRONT_CAMERA
                }

            val analysis =
                ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { imageAnalysis ->
                      imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
                        handleImageFrame(imageProxy)
                      }
                    }

            provider.bindToLifecycle(lifecycleOwner, selector, analysis)
          } catch (error: Exception) {
            emitError(error.message ?: "Camera binding failed")
          }
        },
        ContextCompat.getMainExecutor(context),
    )
  }

  private fun unbindCamera() {
    try {
      cameraProvider?.unbindAll()
    } catch (_: Exception) {
    }
    lastFrameAtMs = 0L
    lastPreviewAtMs = 0L
  }

  private fun handleImageFrame(imageProxy: ImageProxy) {
    try {
      val now = System.currentTimeMillis()
      val shouldPreview = now - lastPreviewAtMs >= PREVIEW_FRAME_INTERVAL_MS
      val shouldEmit = frameIntervalMs > 0 && now - lastFrameAtMs >= frameIntervalMs
      if (!shouldPreview && !shouldEmit) {
        return
      }

      val jpegBytes = imageProxy.toJpegBytes(frameJpegQuality)
      val displayBitmap =
          BitmapFactory.decodeByteArray(jpegBytes, 0, jpegBytes.size)
              ?.toDisplayBitmap(imageProxy.imageInfo.rotationDegrees, cameraFacing == "front")
              ?.scaleToMaxLongEdge(frameMaxLongEdge)

      if (displayBitmap != null && shouldPreview) {
        lastPreviewAtMs = now
        cameraImageView.post { cameraImageView.setImageBitmap(displayBitmap) }
      }

      if (displayBitmap != null && shouldEmit) {
        val emitBytes = displayBitmap.toJpegBytes(frameJpegQuality)
        val jpegBase64 = Base64.encodeToString(emitBytes, Base64.NO_WRAP)
        lastFrameAtMs = now
        val event = Arguments.createMap().apply {
          putString("image", jpegBase64)
          putString("facing", cameraFacing)
          putInt("width", displayBitmap.width)
          putInt("height", displayBitmap.height)
          putInt("rotationDegrees", 0)
        }
        reactContext
            .getJSModule(RCTEventEmitter::class.java)
            .receiveEvent(id, "topFrame", event)
      }
    } catch (error: Exception) {
      emitError(error.message ?: "Camera frame capture failed")
    } finally {
      imageProxy.close()
    }
  }

  private fun emitError(message: String) {
    val event = Arguments.createMap().apply {
      putString("message", message)
      putString("facing", cameraFacing)
    }
    reactContext
        .getJSModule(RCTEventEmitter::class.java)
        .receiveEvent(id, "topCameraError", event)
  }

  private fun ImageProxy.toJpegBytes(quality: Int): ByteArray {
    val nv21 = toNv21()
    val jpegStream = ByteArrayOutputStream()
    YuvImage(nv21, ImageFormat.NV21, width, height, null)
        .compressToJpeg(Rect(0, 0, width, height), quality, jpegStream)
    return jpegStream.toByteArray()
  }

  private fun Bitmap.toDisplayBitmap(rotationDegrees: Int, mirror: Boolean): Bitmap {
    val matrix = Matrix()
    if (rotationDegrees != 0) {
      matrix.postRotate(rotationDegrees.toFloat())
    }
    if (mirror) {
      matrix.postScale(-1f, 1f)
    }
    return Bitmap.createBitmap(this, 0, 0, width, height, matrix, true)
  }

  private fun Bitmap.scaleToMaxLongEdge(maxLongEdge: Int): Bitmap {
    val longEdge = max(width, height)
    if (longEdge <= maxLongEdge) {
      return this
    }

    val scale = maxLongEdge.toFloat() / longEdge.toFloat()
    val targetWidth = max(1, (width * scale).toInt())
    val targetHeight = max(1, (height * scale).toInt())
    return Bitmap.createScaledBitmap(this, targetWidth, targetHeight, true)
  }

  private fun Bitmap.toJpegBytes(quality: Int): ByteArray {
    val jpegStream = ByteArrayOutputStream()
    compress(Bitmap.CompressFormat.JPEG, quality, jpegStream)
    return jpegStream.toByteArray()
  }

  private fun ImageProxy.toNv21(): ByteArray {
    val yPlane = planes[0]
    val uPlane = planes[1]
    val vPlane = planes[2]
    val ySize = width * height
    val uvSize = width * height / 4
    val output = ByteArray(ySize + uvSize * 2)

    copyPlane(
        yPlane.buffer,
        yPlane.rowStride,
        yPlane.pixelStride,
        width,
        height,
        output,
        0,
        1,
    )

    val chromaWidth = width / 2
    val chromaHeight = height / 2
    val uBuffer = uPlane.buffer
    val vBuffer = vPlane.buffer
    val uRowStride = uPlane.rowStride
    val vRowStride = vPlane.rowStride
    val uPixelStride = uPlane.pixelStride
    val vPixelStride = vPlane.pixelStride
    var outputIndex = ySize

    for (row in 0 until chromaHeight) {
      val uRowOffset = row * uRowStride
      val vRowOffset = row * vRowStride
      for (column in 0 until chromaWidth) {
        output[outputIndex++] = vBuffer.get(vRowOffset + column * vPixelStride)
        output[outputIndex++] = uBuffer.get(uRowOffset + column * uPixelStride)
      }
    }

    return output
  }

  private fun copyPlane(
      source: java.nio.ByteBuffer,
      rowStride: Int,
      pixelStride: Int,
      width: Int,
      height: Int,
      output: ByteArray,
      outputOffset: Int,
      outputPixelStride: Int,
  ) {
    var outputIndex = outputOffset
    for (row in 0 until height) {
      val rowOffset = row * rowStride
      for (column in 0 until width) {
        output[outputIndex] = source.get(rowOffset + column * pixelStride)
        outputIndex += outputPixelStride
      }
    }
  }

  private companion object {
    private const val PREVIEW_FRAME_INTERVAL_MS = 80L
  }
}
