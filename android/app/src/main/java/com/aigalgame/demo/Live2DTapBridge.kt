package com.aigalgame.demo

import androidx.compose.runtime.Stable

@Stable
class Live2DTapBridge {
    private var handler: ((Float, Float) -> Unit)? = null

    fun setHandler(handler: ((Float, Float) -> Unit)?) {
        this.handler = handler
    }

    fun dispatch(normalizedX: Float, normalizedY: Float) {
        handler?.invoke(normalizedX.coerceIn(0f, 1f), normalizedY.coerceIn(0f, 1f))
    }
}
