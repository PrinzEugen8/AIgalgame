package com.aigalgame.demo

import kotlin.math.max

object Live2DHitTest {
    private const val HorizontalRange = 480f
    private const val VerticalRange = 900f
    private const val BottomInsetRange = 650f
    private const val CharacterCenterX = 0.5f
    private const val CharacterCenterY = 0.42f

    fun mapScreenToModelSpace(
        screenX: Float,
        screenY: Float,
        placement: OutfitPlacement
    ): Pair<Float, Float> {
        val scale = placement.scale.coerceIn(0.25f, 4f)
        val offsetXNorm = placement.offsetX / HorizontalRange * 0.14f
        val offsetYNorm = placement.offsetY / VerticalRange * 0.11f
        val bottomLift = placement.bottomInset / BottomInsetRange * 0.07f

        var x = screenX - offsetXNorm
        var y = screenY + bottomLift - offsetYNorm
        x = CharacterCenterX + (x - CharacterCenterX) / max(scale, 0.25f)
        y = CharacterCenterY + (y - CharacterCenterY) / max(scale, 0.25f)
        return x.coerceIn(0f, 1f) to y.coerceIn(0f, 1f)
    }
}
