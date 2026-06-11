package com.aigalgame.demo

import kotlin.math.max

object Live2DHitTest {
    private const val HorizontalRange = 480f
    private const val VerticalRange = 900f
    private const val BottomInsetRange = 650f
    private const val CharacterCenterX = 0.5f
    private const val CharacterCenterY = 0.42f
    const val ModelCoordMin = -0.5f
    const val ModelCoordMax = 1.5f

    fun mapScreenToModelSpace(
        screenX: Float,
        screenY: Float,
        placement: OutfitPlacement
    ): Pair<Float, Float> {
        val (x, y) = projectScreenToModel(screenX, screenY, placement)
        return x.coerceIn(ModelCoordMin, ModelCoordMax) to y.coerceIn(ModelCoordMin, ModelCoordMax)
    }

    fun mapModelToScreenSpace(
        modelX: Float,
        modelY: Float,
        placement: OutfitPlacement
    ): Pair<Float, Float> {
        val (x, y) = projectModelToScreen(modelX, modelY, placement)
        return x.coerceIn(0f, 1f) to y.coerceIn(0f, 1f)
    }

    fun mapModelToScreenSpaceRaw(
        modelX: Float,
        modelY: Float,
        placement: OutfitPlacement
    ): Pair<Float, Float> = projectModelToScreen(modelX, modelY, placement)

    private fun projectScreenToModel(
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
        return x to y
    }

    private fun projectModelToScreen(
        modelX: Float,
        modelY: Float,
        placement: OutfitPlacement
    ): Pair<Float, Float> {
        val scale = placement.scale.coerceIn(0.25f, 4f)
        val offsetXNorm = placement.offsetX / HorizontalRange * 0.14f
        val offsetYNorm = placement.offsetY / VerticalRange * 0.11f
        val bottomLift = placement.bottomInset / BottomInsetRange * 0.07f

        var x = CharacterCenterX + (modelX - CharacterCenterX) * max(scale, 0.25f)
        var y = CharacterCenterY + (modelY - CharacterCenterY) * max(scale, 0.25f)
        x += offsetXNorm
        y = y + offsetYNorm - bottomLift
        return x to y
    }
}
