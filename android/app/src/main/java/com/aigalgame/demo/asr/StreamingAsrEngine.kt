package com.aigalgame.demo.asr

interface AsrListener {
    fun onStatus(status: String)
    fun onPartial(text: String)
    fun onFinal(text: String)
    fun onError(error: Throwable)
}

interface StreamingAsrEngine {
    fun start()
    fun stop()
    fun release()
}
