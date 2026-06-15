package com.aigalgame.demo

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Live2DConfigTest {
    @Test
    fun defaultCharacterIsNekoLive2D() {
        assertEquals("neko", Live2DCharacterConfigs.DefaultCharacter)

        val config = Live2DCharacterConfigs.forCharacter("missing")

        assertEquals("neko", config.character)
        assertEquals(CharacterRendererMode.Live2D, config.rendererMode)
        assertEquals("atri", config.staticFallbackCharacter)
        assertEquals("live2d/models/neko/neko.model3.json", config.modelAssetPath)
    }

    @Test
    fun nekoRequiredAssetsArePackaged() {
        val required = Live2DCharacterConfigs.NekoRequiredModelAssetPaths +
            Live2DCharacterConfigs.OfficialShaderAssetPath

        required.forEach { path ->
            assertTrue("Missing asset $path", File("src/main/assets/$path").exists())
        }
        assertTrue(File("libs/Live2DCubismCore.aar").exists())
        assertTrue(File("src/main/java/com/live2d/sdk/cubism/framework/CubismFramework.java").exists())
        assertTrue(File("src/main/java/com/live2d/sdk/cubism/framework/rendering/android/CubismRendererAndroid.java").exists())
    }

    @Test
    fun staticCharactersStayStaticPng() {
        val atri = Live2DCharacterConfigs.forCharacter("atri")
        val murasame = Live2DCharacterConfigs.forCharacter("murasame")

        assertEquals(CharacterRendererMode.StaticPng, atri.rendererMode)
        assertEquals(CharacterRendererMode.StaticPng, murasame.rendererMode)
        assertEquals(R.drawable.character_atri_idle, characterImageRes("atri", "calm", "idle"))
        assertEquals(R.drawable.character_murasame_happy, characterImageRes("murasame", "happy", "idle"))
    }

    @Test
    fun obsoleteWebRendererAssetsAreGone() {
        assertFalse(File("src/main/assets/live2d-web").exists())
        assertFalse(File("src/main/assets/live2d/models/Haru").exists())
        assertFalse(File("src/main/assets/live2d-web/vendor/pixi.min.js").exists())
        assertFalse(File("src/main/assets/live2d-web/vendor/cubism4.min.js").exists())
    }

    @Test
    fun live2DStageUsesOfficialRendererAndPngFallback() {
        val stageKt = File("src/main/java/com/aigalgame/demo/Live2DStage.kt").readText()
        val configKt = File("src/main/java/com/aigalgame/demo/Live2DConfig.kt").readText()

        listOf(stageKt, configKt).forEach { source ->
            assertFalse(source.contains("WebView"))
            assertFalse(source.contains("android.webkit"))
            assertFalse(source.contains("live2d-web"))
            assertFalse(source.contains("pixi", ignoreCase = true))
            assertFalse(source.contains("Haru"))
        }
        assertTrue(stageKt.contains("OfficialLive2DView"))
        assertTrue(stageKt.contains("CharacterStandee("))
        assertTrue(File("src/main/java/com/aigalgame/demo/live2d/PersistentLive2DEngine.java").exists())
    }

    @Test
    fun observationHomeKeepsLive2DRendererAvailableForDressUp() {
        val stageKt = File("src/main/java/com/aigalgame/demo/Live2DStage.kt").readText()
        val mainKt = File("src/main/java/com/aigalgame/demo/MainActivity.kt").readText()
        val viewJava = File("src/main/java/com/aigalgame/demo/live2d/OfficialLive2DView.java").readText()
        val engineJava = File("src/main/java/com/aigalgame/demo/live2d/PersistentLive2DEngine.java").readText()

        assertTrue(mainKt.contains("Live2DBootLoadingScreen"))
        assertTrue(mainKt.contains("val showLive2DStage = vm.screen == AppScreen.DressUp"))
        assertTrue(mainKt.contains("if (showLive2DStage)"))
        assertTrue(mainKt.contains("if (showLive2DStage && !vm.live2dBootReady)"))
        assertTrue(mainKt.contains("ObservationSceneBackground"))
        assertTrue(mainKt.contains("ObservationMessageDrawer"))
        assertTrue(mainKt.contains("QuickReplyFillStrip"))
        assertTrue(mainKt.contains("R.drawable.bg_catgirl_typing"))
        assertTrue(File("src/main/res/drawable-nodpi/bg_catgirl_typing.png").exists())
        assertTrue(File("src/main/res/drawable-nodpi/bg_catgirl_reading.png").exists())
        assertTrue(File("src/main/res/drawable-nodpi/bg_catgirl_gaming.png").exists())
        assertTrue(File("src/main/res/drawable-nodpi/bg_catgirl_music.png").exists())
        assertFalse(mainKt.contains("stageOnPrimaryScreens"))
        assertTrue(mainKt.contains("Live2DStage("))
        assertTrue(mainKt.contains(".zIndex(1f)"))
        assertTrue(mainKt.contains("editable = false"))
        assertFalse(mainKt.contains("padding(top = 104.dp, end = 18.dp)"))
        assertFalse(mainKt.contains("padding(start = 16.dp, top = 88.dp, end = 16.dp)"))
        assertTrue(stageKt.contains("showCharacter"))
        assertTrue(stageKt.contains("live2DVisible"))
        assertTrue(stageKt.contains("clearStageListener()"))
        assertFalse(stageKt.contains("releaseRenderer()"))
        assertTrue(viewJava.contains("PersistentLive2DEngine.getInstance"))
        assertTrue(viewJava.contains("engine.pauseFrames()"))
        assertTrue(viewJava.contains("return false;"))
        assertFalse(viewJava.contains("stopRenderThread"))
        assertTrue(engineJava.contains("void pauseFrames()"))
        assertTrue(engineJava.contains("void resumeFrames()"))
        assertTrue(engineJava.contains("void detachSurface"))
    }

    @Test
    fun renderCommandFieldsMatchRendererContract() {
        val command = Live2DRenderCommand(
            characterId = "neko",
            emotion = "happy",
            motion = "TapHead",
            mouthOpen = 0.4f,
            speaking = true,
            lookX = 0.2f,
            lookY = -0.1f,
            placement = OutfitPlacement(scale = 1.1f),
            interactive = true,
            commandNonce = 42L
        )

        assertEquals("neko", command.characterId)
        assertEquals("happy", command.emotion)
        assertEquals("TapHead", command.motion)
        assertEquals(0.4f, command.mouthOpen, 0.001f)
        assertTrue(command.speaking)
        assertEquals(0.2f, command.lookX, 0.001f)
        assertEquals(-0.1f, command.lookY, 0.001f)
        assertEquals(1.1f, command.placement.scale, 0.001f)
        assertTrue(command.interactive)
        assertEquals(42L, command.commandNonce)
    }

    @Test
    fun hitAreasAndReactionsStayConfigDriven() {
        val config = Live2DCharacterConfigs.forCharacter("neko")

        assertEquals("head", config.hitAreas.first { it.contains(0.5f, 0.2f) }.id)
        assertEquals("chest", config.hitAreas.first { it.contains(0.5f, 0.4f) }.id)
        assertEquals("hand", config.hitAreas.first { it.contains(0.35f, 0.5f) }.id)
        assertTrue(config.reactions.any { it.hitArea == "chest" && it.intensity == Live2DReactionIntensity.Flirty })
        assertTrue(config.reactions.any { it.hitArea == "chest" && it.intensity == Live2DReactionIntensity.Boundary })
    }

    @Test
    fun stageTapLayerAppliesModifierChain() {
        val stageKt = File("src/main/java/com/aigalgame/demo/Live2DStage.kt").readText()
        assertTrue(stageKt.contains("modifier = modifier.pointerInput"))
        assertTrue(stageKt.contains("fun CharacterTapZone"))
    }

    @Test
    fun hitTestMapsPlacementAdjustedCoordinates() {
        val placement = OutfitPlacement()
        val centered = Live2DHitTest.mapScreenToModelSpace(0.5f, 0.4f, placement)
        val shifted = Live2DHitTest.mapScreenToModelSpace(0.5f, 0.4f, OutfitPlacement(scale = 1.4f, offsetX = 40f))
        assertTrue(centered.first in 0.3f..0.7f)
        assertTrue(shifted.first != centered.first || shifted.second != centered.second)
        val roundTrip = Live2DHitTest.mapModelToScreenSpace(centered.first, centered.second, placement)
        assertEquals(centered.first, roundTrip.first, 0.02f)
        assertEquals(centered.second, roundTrip.second, 0.02f)
    }

    @Test
    fun selfTestIncludesHitAreaDebugOverlay() {
        val stageKt = File("src/main/java/com/aigalgame/demo/Live2DStage.kt").readText()
        assertTrue(stageKt.contains("HitAreaDebugOverlay"))
        assertTrue(stageKt.contains("mapModelToScreenSpace"))
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
