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
        assertFalse(File("src/main/assets/live2d/samples").exists())
        assertFalse(stageJs.contains("setPixiBackground"))
        assertTrue(stageJs.contains("getLocalBounds"))
        assertTrue(stageJs.contains("pivot.set"))
        assertTrue(stageJs.contains("pixi-cubism-runtime-v10-transparent-dom-fallback"))
        assertTrue(stageJs.contains("transparent: true"))
        assertTrue(stageJs.contains("backgroundAlpha: 0"))
        assertTrue(stageJs.contains("app.renderer.backgroundAlpha = 0"))
        assertTrue(stageJs.contains("webBackgroundDisabled: true"))
        assertTrue(stageJs.contains("preferWebGLVersion: 1"))
        assertTrue(stageJs.contains("PIXI.settings.PREFER_ENV"))
        assertTrue(stageJs.contains("ENABLE_DOM_PRESENTER = true"))
        assertTrue(stageJs.contains("MODEL_PIXEL_THRESHOLD"))
        assertTrue(stageJs.contains("modelPixelReady"))
        assertTrue(stageJs.contains("waiting-model-pixels"))
        assertTrue(stageJs.contains("notifyPresented(\"dom-presenter\")"))
        assertTrue(stageJs.contains("domPresenterEnabled"))
        assertTrue(stageJs.contains("onPresented"))
        assertFalse(stageJs.contains("modelBaseHeight"))
        assertFalse(stageJs.contains("modelBaseWidth"))

        val indexHtml = File("src/main/assets/live2d-web/index.html").readText()
        assertTrue(indexHtml.contains("pixi-cubism-runtime-v10-transparent-dom-fallback"))
        assertTrue(indexHtml.contains("fallback-image"))
        assertTrue(indexHtml.contains("present-canvas"))
        assertTrue(indexHtml.contains("present-image"))
        assertTrue(indexHtml.contains("model-pixels"))
        assertFalse(
            Regex(
                "#present-(canvas|image)\\s*\\{[^}]*display\\s*:\\s*none",
                setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)
            ).containsMatchIn(indexHtml)
        )
        assertTrue(indexHtml.contains("src=\"./fallbacks/haru-stage.png\""))
        assertTrue(stageJs.contains("presentImage.removeAttribute(\"src\")"))
        assertTrue(stageJs.contains("presentCanvas.style.display = \"block\""))
        assertTrue(stageJs.contains("presentImage.style.display = \"block\""))
        assertTrue(stageJs.contains("setFallbackVisible(true)"))
        assertFalse(stageJs.contains("setFallbackVisible(false)"))
        assertFalse(stageJs.contains("setFallbackVisible(!visible)"))
        assertTrue(File("src/main/assets/live2d-web/fallbacks/haru-stage.png").exists())
        assertTrue(File("src/main/res/drawable-nodpi/live2d_haru_fallback.png").exists())
    }

    @Test
    fun webLive2DStageKeepsNativeVisibilityGuard() {
        val stageKt = File("src/main/java/com/aigalgame/demo/Live2DStage.kt").readText()

        assertTrue(stageKt.contains("val useWebStage = state.canRenderLive2D && !webStageFailed"))
        assertTrue(stageKt.contains("val useNativeVisibilityGuard = stageMode == \"home\""))
        assertTrue(stageKt.contains("val showNativeFallback = !useWebStage || !webStagePresented || useNativeVisibilityGuard"))
        assertTrue(stageKt.contains("pixi-cubism-runtime-v10-transparent-dom-fallback"))
        assertTrue(stageKt.contains("private const val Live2DWebSurfaceColor = 0x00000000"))
        assertTrue(stageKt.contains("Live2DWebStage("))
        assertTrue(stageKt.contains("webStagePresented = true"))
        assertTrue(stageKt.contains("delay(4500L)"))
        assertTrue(stageKt.contains("NativeHaruLive2DFallback"))
        assertTrue(stageKt.contains("if (showNativeFallback)"))
        assertTrue(stageKt.contains("if (!editable && showNativeFallback)"))
        assertTrue(stageKt.contains("R.drawable.live2d_haru_fallback_00"))
        assertTrue(stageKt.contains("Log.d(Live2DWebTag, \"presented\")"))
        assertTrue(stageKt.contains("onPresented = { Log.d(Live2DWebTag, \"selftest presented\") }"))
        assertTrue(stageKt.contains("private fun NativeHaruLive2DFallback(\n    placement: OutfitPlacement = OutfitPlacement()"))
        assertTrue(stageKt.contains("baseHeight * nativePlacement.scale"))
        assertTrue(stageKt.contains(".offset(x = nativePlacement.offsetX.dp, y = nativePlacement.offsetY.dp)"))
        assertFalse(stageKt.contains("CharacterStandee("))
        assertTrue(stageKt.contains("NativeHaruFallbackFrames"))
        assertTrue(stageKt.contains("R.drawable.live2d_haru_fallback_07"))
        assertTrue(stageKt.contains("delay(160L)"))
        assertEquals(3, Regex("NativeHaruLive2DFallback\\(").findAll(stageKt).count())
        (0..7).forEach { index ->
            assertTrue(File("src/main/res/drawable-nodpi/live2d_haru_fallback_%02d.png".format(index)).exists())
        }
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
