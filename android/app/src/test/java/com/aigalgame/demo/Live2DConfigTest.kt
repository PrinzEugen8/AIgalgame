package com.aigalgame.demo

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Live2DConfigTest {
    @Test
    fun charactersUseHaruAsFallbackModel() {
        assertEquals(
            "live2d/models/Haru/Haru.model3.json",
            Live2DCharacterConfigs.forCharacter("atri").fallbackModelAssetPath
        )
        assertEquals(
            "live2d/models/Haru/Haru.model3.json",
            Live2DCharacterConfigs.forCharacter("murasame").fallbackModelAssetPath
        )
    }

    @Test
    fun modelConfigUsesOfflineHaruSample() {
        val config = Live2DCharacterConfigs.forCharacter("atri").modelConfig

        assertEquals("Haru", config.name)
        assertEquals("live2d/models/Haru/Haru.model3.json", config.assetPath)
        assertEquals("Idle", config.idleMotionGroupName)
        assertTrue(config.emotionMap.containsKey("happy"))
        assertTrue(config.tapMotions.any { it.hitArea == "head" })
    }

    @Test
    fun runtimeUsesPackagedPixiStageAssets() {
        assertEquals("live2d-web/index.html", Live2DCharacterConfigs.OfficialStageAssetPath)
        assertEquals("live2d-web/vendor/live2dcubismcore.min.js", Live2DCharacterConfigs.OfficialCoreAssetPath)
        assertTrue(Live2DCharacterConfigs.RequiredRuntimeAssetPaths.contains("live2d-web/vendor/pixi.min.js"))
        assertTrue(Live2DCharacterConfigs.RequiredRuntimeAssetPaths.contains("live2d-web/vendor/cubism4.min.js"))
    }

    @Test
    fun webStageUsesSinglePackagedModelEntry() {
        val stageJs = File("src/main/assets/live2d-web/stage.js").readText()

        assertTrue(stageJs.contains("live2d/models/Haru/Haru.model3.json"))
        assertFalse(stageJs.contains("live2d/samples/Haru"))
        assertFalse(stageJs.contains("setPixiBackground"))
    }

    @Test
    fun hitAreasCoverExpectedTouchRegions() {
        val config = Live2DCharacterConfigs.forCharacter("atri")

        assertEquals("head", config.hitAreas.first { it.contains(0.5f, 0.2f) }.id)
        assertEquals("chest", config.hitAreas.first { it.contains(0.5f, 0.4f) }.id)
        assertEquals("hand", config.hitAreas.first { it.contains(0.2f, 0.5f) }.id)
    }

    @Test
    fun sensitiveAreaReactionsStayConfigDriven() {
        val reactions = Live2DCharacterConfigs.forCharacter("atri")
            .reactions
            .filter { it.hitArea == "chest" }

        assertTrue(reactions.any { it.intensity == Live2DReactionIntensity.Flirty })
        assertTrue(reactions.any { it.intensity == Live2DReactionIntensity.Boundary })
        assertTrue(reactions.all { it.localTextCandidates.isNotEmpty() })
    }

    @Test
    fun placementCoercionKeepsValuesInsideStageRange() {
        val coerced = OutfitPlacement(
            scale = 9f,
            offsetX = 9999f,
            offsetY = -9999f,
            bottomInset = 999f
        ).coerceForStage()

        assertEquals(4.0f, coerced.scale, 0.001f)
        assertEquals(1920f, coerced.offsetX, 0.001f)
        assertEquals(-3600f, coerced.offsetY, 0.001f)
        assertEquals(220f, coerced.bottomInset, 0.001f)
    }
}
