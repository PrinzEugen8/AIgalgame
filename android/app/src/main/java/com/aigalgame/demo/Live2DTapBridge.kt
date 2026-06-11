package com.aigalgame.demo

import androidx.compose.runtime.Stable

@Stable
class Live2DTapBridge {
    private var handler: ((Float, Float) -> Unit)? = null
    private var gazeHandler: ((Float, Float) -> Unit)? = null

    fun setHandler(handler: ((Float, Float) -> Unit)?) {
        this.handler = handler
    }

    fun setGazeHandler(handler: ((Float, Float) -> Unit)?) {
        this.gazeHandler = handler
    }

    fun dispatch(normalizedX: Float, normalizedY: Float) {
        val x = normalizedX.coerceIn(0f, 1f)
        val y = normalizedY.coerceIn(0f, 1f)
        handler?.invoke(x, y)
    }

    fun dispatchGaze(normalizedX: Float, normalizedY: Float) {
        val x = normalizedX.coerceIn(0f, 1f)
        val y = normalizedY.coerceIn(0f, 1f)
        gazeHandler?.invoke(x, y)
    }
}
