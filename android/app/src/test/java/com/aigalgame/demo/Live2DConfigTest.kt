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
        assertEquals("hand", config.hitAreas.first { it.contains(0.2f, 0.5f) }.id)
        assertTrue(config.reactions.any { it.hitArea == "chest" && it.intensity == Live2DReactionIntensity.Flirty })
        assertTrue(config.reactions.any { it.hitArea == "chest" && it.intensity == Live2DReactionIntensity.Boundary })
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
