package com.aigalgame.demo

object Live2DCharacterConfigs {
    const val DefaultCharacter = "neko"
    const val OfficialCoreAarProjectPath = "libs/Live2DCubismCore.aar"
    const val OfficialShaderAssetPath = "com/live2d/sdk/cubism/framework/shaders/standardES/VertShaderSrc.vert"
    const val DefaultModelAssetPath = "live2d/models/neko/neko.model3.json"
    const val NekoModelDirectory = "live2d/models/neko/"

    val NekoRequiredModelAssetPaths = listOf(
        "live2d/models/neko/neko.model3.json",
        "live2d/models/neko/neko.moc3",
        "live2d/models/neko/neko.physics3.json",
        "live2d/models/neko/neko.cdi3.json",
        "live2d/models/neko/neko.4096/texture_00.png",
        "live2d/models/neko/neko.4096/texture_01.png",
        "live2d/models/neko/neko.4096/texture_02.png",
        "live2d/models/neko/neko.4096/texture_03.png",
        "live2d/models/neko/neko.4096/texture_04.png",
        "live2d/models/neko/neko.4096/texture_05.png"
    )

    val RequiredRuntimeAssetPaths = listOf(OfficialShaderAssetPath) + NekoRequiredModelAssetPaths

    private val semanticBindings = mapOf(
        "calm" to Live2DEmotionBinding(expression = "calm", motion = "idle"),
        "happy" to Live2DEmotionBinding(expression = "happy", motion = "happy"),
        "thinking" to Live2DEmotionBinding(expression = "thinking", motion = "thinking"),
        "shy" to Live2DEmotionBinding(expression = "shy", motion = "shy"),
        "sad" to Live2DEmotionBinding(expression = "sad", motion = "sad"),
        "angry" to Live2DEmotionBinding(expression = "angry", motion = "angry"),
        "sleep" to Live2DEmotionBinding(expression = "sleep", motion = "idle")
    )

    private val semanticPoseMotionMap = mapOf(
        "idle" to "idle",
        "happy" to "happy",
        "thinking" to "thinking",
        "shy" to "shy",
        "sad" to "sad",
        "angry" to "angry",
        "sleep" to "idle"
    )

    private val nekoEmotionMap = mapOf(
        "calm" to "calm",
        "happy" to "happy",
        "shy" to "shy",
        "sad" to "sad",
        "thinking" to "thinking",
        "angry" to "angry",
        "sleep" to "calm"
    )

    private val nekoTapMotions = listOf(
        Live2DTapMotionConfig("head", "TapHead", 1.0f),
        Live2DTapMotionConfig("head", "idle", 0.25f),
        Live2DTapMotionConfig("chest", "TapChest", 1.0f),
        Live2DTapMotionConfig("chest", "StepBack", 0.35f),
        Live2DTapMotionConfig("hand", "TapHand", 1.0f),
        Live2DTapMotionConfig("hand", "happy", 0.3f),
        Live2DTapMotionConfig("body", "TapBody", 1.0f),
        Live2DTapMotionConfig("body", "thinking", 0.3f)
    )

    private val nekoModelConfig = Live2DModelConfig(
        name = "NEKO",
        assetPath = DefaultModelAssetPath,
        scale = 1.0f,
        initialXShift = 0f,
        initialYShift = 0f,
        idleMotionGroupName = "",
        emotionMap = nekoEmotionMap,
        tapMotions = nekoTapMotions
    )

    private val defaultHitAreas = listOf(
        Live2DHitArea("head", "head", left = 0.38f, top = 0.05f, right = 0.62f, bottom = 0.26f, priority = 40),
        Live2DHitArea("chest", "chest", left = 0.43f, top = 0.30f, right = 0.57f, bottom = 0.42f, priority = 30),
        Live2DHitArea("hand", "hand", left = 0.28f, top = 0.40f, right = 0.72f, bottom = 0.66f, priority = 25),
        Live2DHitArea("body", "body", left = 0.34f, top = 0.26f, right = 0.66f, bottom = 0.78f, priority = 10)
    )

    private val defaultReactions = listOf(
        Live2DReactionConfig(
            hitArea = "head",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHead",
            expression = "happy",
            cooldownMs = 1200L
        ),
        Live2DReactionConfig(
            hitArea = "chest",
            intensity = Live2DReactionIntensity.Flirty,
            motion = "TapChest",
            expression = "shy",
            cooldownMs = 2200L
        ),
        Live2DReactionConfig(
            hitArea = "chest",
            intensity = Live2DReactionIntensity.Boundary,
            motion = "StepBack",
            expression = "shy",
            cooldownMs = 2600L
        ),
        Live2DReactionConfig(
            hitArea = "hand",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHand",
            expression = "happy",
            cooldownMs = 1300L
        ),
        Live2DReactionConfig(
            hitArea = "body",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapBody",
            expression = "thinking",
            cooldownMs = 1300L
        )
    )

    private val configs = mapOf(
        "neko" to Live2DCharacterConfig(
            character = "neko",
            rendererMode = CharacterRendererMode.Live2D,
            staticFallbackCharacter = "atri",
            modelAssetPath = DefaultModelAssetPath,
            fallbackModelAssetPath = "",
            modelConfig = nekoModelConfig,
            emotionBindings = semanticBindings,
            poseMotionMap = semanticPoseMotionMap,
            hitAreas = defaultHitAreas,
            reactions = defaultReactions
        ),
        "atri" to Live2DCharacterConfig(
            character = "atri",
            rendererMode = CharacterRendererMode.StaticPng,
            staticFallbackCharacter = "atri",
            modelAssetPath = "",
            emotionBindings = semanticBindings,
            poseMotionMap = semanticPoseMotionMap,
            hitAreas = defaultHitAreas,
            reactions = defaultReactions
        ),
        "murasame" to Live2DCharacterConfig(
            character = "murasame",
            rendererMode = CharacterRendererMode.StaticPng,
            staticFallbackCharacter = "murasame",
            modelAssetPath = "",
            emotionBindings = semanticBindings,
            poseMotionMap = semanticPoseMotionMap,
            hitAreas = defaultHitAreas,
            reactions = defaultReactions
        )
    )

    fun forCharacter(character: String): Live2DCharacterConfig {
        return configs[character] ?: configs.getValue(DefaultCharacter)
    }
}
