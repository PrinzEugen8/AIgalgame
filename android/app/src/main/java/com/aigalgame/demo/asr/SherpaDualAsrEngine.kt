package com.aigalgame.demo.asr

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.EndpointRule
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.getFeatureConfig
import com.k2fsa.sherpa.onnx.getModelConfig
import com.k2fsa.sherpa.onnx.getOfflineModelConfig
import kotlin.concurrent.thread

class SherpaDualAsrEngine(
    private val context: Context,
    private val listener: AsrListener,
) : StreamingAsrEngine {
    private val sampleRate = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT

    private var onlineRecognizer: OnlineRecognizer? = null
    private var finalRecognizer: OfflineRecognizer? = null
    private var audioRecord: AudioRecord? = null
    private var worker: Thread? = null

    @Volatile
    private var running = false

    override fun start() {
        if (running) return
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            listener.onError(SecurityException("RECORD_AUDIO permission is not granted"))
            return
        }

        try {
            ensureRecognizers()
            val minBytes = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                sampleRate,
                channelConfig,
                audioFormat,
                minBytes.coerceAtLeast(sampleRate / 5) * 2,
            )
            audioRecord?.startRecording()
            running = true
            listener.onStatus("listening")
            worker = thread(start = true, isDaemon = true, name = "sherpa-asr") {
                runLoop()
            }
        } catch (error: Throwable) {
            running = false
            listener.onError(error)
        }
    }

    override fun stop() {
        running = false
        try {
            audioRecord?.stop()
        } catch (_: Exception) {
        }
        audioRecord?.release()
        audioRecord = null
        worker = null
        listener.onStatus("stopped")
    }

    override fun release() {
        stop()
        onlineRecognizer?.release()
        onlineRecognizer = null
        finalRecognizer?.release()
        finalRecognizer = null
    }

    private fun ensureRecognizers() {
        if (onlineRecognizer == null) {
            listener.onStatus("loading Paraformer")
            val onlineConfig = OnlineRecognizerConfig(
                featConfig = getFeatureConfig(sampleRate = sampleRate, featureDim = 80),
                modelConfig = getModelConfig(type = 5) ?: error("missing streaming Paraformer config"),
                endpointConfig = EndpointConfig(
                    rule1 = EndpointRule(false, 2.4f, 0.0f),
                    rule2 = EndpointRule(true, 0.8f, 0.0f),
                    rule3 = EndpointRule(false, 0.0f, 5.0f),
                ),
                enableEndpoint = true,
            )
            onlineRecognizer = OnlineRecognizer(context.assets, onlineConfig)
        }
        if (finalRecognizer == null) {
            listener.onStatus("loading SenseVoice")
            val modelConfig = getOfflineModelConfig(type = 15) ?: error("missing SenseVoice config")
            modelConfig.senseVoice.language = "zh"
            modelConfig.senseVoice.useInverseTextNormalization = true
            val offlineConfig = OfflineRecognizerConfig(
                featConfig = getFeatureConfig(sampleRate = sampleRate, featureDim = 80),
                modelConfig = modelConfig,
            )
            finalRecognizer = OfflineRecognizer(context.assets, offlineConfig)
        }
    }

    private fun runLoop() {
        val recognizer = onlineRecognizer ?: return
        val stream = recognizer.createStream()
        val buffer = ShortArray((0.1 * sampleRate).toInt())
        val utterance = ArrayList<Float>(sampleRate * 8)
        var lastPartial = ""
        try {
            while (running) {
                val read = audioRecord?.read(buffer, 0, buffer.size) ?: break
                if (read <= 0) continue

                val samples = FloatArray(read) { buffer[it] / 32768.0f }
                appendBounded(utterance, samples, sampleRate * 30)
                stream.acceptWaveform(samples, sampleRate)
                while (recognizer.isReady(stream)) {
                    recognizer.decode(stream)
                }

                var text = recognizer.getResult(stream).text.trim()
                if (text.isNotEmpty() && text != lastPartial) {
                    lastPartial = text
                    listener.onPartial(text)
                }

                if (recognizer.isEndpoint(stream)) {
                    if (recognizer.config.modelConfig.paraformer.encoder.isNotBlank()) {
                        val tailPaddings = FloatArray((0.8 * sampleRate).toInt())
                        stream.acceptWaveform(tailPaddings, sampleRate)
                        while (recognizer.isReady(stream)) {
                            recognizer.decode(stream)
                        }
                        text = recognizer.getResult(stream).text.trim()
                    }
                    val finalText = refineFinal(utterance.toFloatArray()).ifBlank { text }
                    if (finalText.isNotBlank()) {
                        listener.onFinal(finalText)
                    }
                    utterance.clear()
                    lastPartial = ""
                    recognizer.reset(stream)
                }
            }
        } catch (error: Throwable) {
            if (running) listener.onError(error)
        } finally {
            stream.release()
        }
    }

    private fun refineFinal(samples: FloatArray): String {
        if (samples.isEmpty()) return ""
        val recognizer = finalRecognizer ?: return ""
        val stream = recognizer.createStream()
        return try {
            stream.acceptWaveform(samples, sampleRate)
            recognizer.decode(stream)
            recognizer.getResult(stream).text.trim()
        } catch (_: Throwable) {
            ""
        } finally {
            stream.release()
        }
    }

    private fun appendBounded(target: ArrayList<Float>, samples: FloatArray, maxSamples: Int) {
        for (sample in samples) target.add(sample)
        val overflow = target.size - maxSamples
        if (overflow > 0) {
            target.subList(0, overflow).clear()
        }
    }
}
