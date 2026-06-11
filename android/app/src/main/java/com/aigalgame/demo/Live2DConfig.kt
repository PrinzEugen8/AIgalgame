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

    private val nekoModelConfig = Live2DModelConfig(
        name = "NEKO",
        assetPath = DefaultModelAssetPath,
        scale = 1.0f,
        initialXShift = 0f,
        initialYShift = 0f,
        idleMotionGroupName = "",
        emotionMap = emptyMap(),
        tapMotions = emptyList()
    )

    private val defaultHitAreas = listOf(
        Live2DHitArea("head", "head", left = 0.34f, top = 0.08f, right = 0.66f, bottom = 0.30f),
        Live2DHitArea("chest", "chest", left = 0.37f, top = 0.34f, right = 0.63f, bottom = 0.52f),
        Live2DHitArea("hand", "hand", left = 0.18f, top = 0.42f, right = 0.82f, bottom = 0.70f),
        Live2DHitArea("body", "body", left = 0.30f, top = 0.30f, right = 0.70f, bottom = 0.82f)
    )

    private val defaultReactions = listOf(
        Live2DReactionConfig(
            hitArea = "head",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHead",
            expression = "happy",
            localTextCandidates = listOf(
                "Hey, be gentle with my head.",
                "That is warm. I do not hate it."
            ),
            relationDelta = RelationDelta(affection = 1, mood = 1),
            cooldownMs = 1200L
        ),
        Live2DReactionConfig(
            hitArea = "chest",
            intensity = Live2DReactionIntensity.Flirty,
            motion = "TapChest",
            expression = "shy",
            localTextCandidates = listOf(
                "You are getting bold today.",
                "That spot makes me a little embarrassed.",
                "If you come closer, I will look right back at you."
            ),
            relationDelta = RelationDelta(affection = 1, dependency = 1, mood = 1),
            cooldownMs = 2200L
        ),
        Live2DReactionConfig(
            hitArea = "chest",
            intensity = Live2DReactionIntensity.Boundary,
            motion = "StepBack",
            expression = "shy",
            localTextCandidates = listOf(
                "Wait a second, go slower here.",
                "I am not upset. It was just sudden."
            ),
            relationDelta = RelationDelta(trust = -1, mood = -1),
            cooldownMs = 2600L
        ),
        Live2DReactionConfig(
            hitArea = "hand",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHand",
            expression = "happy",
            localTextCandidates = listOf(
                "Do you want to hold hands for a while?",
                "Your hand feels warm."
            ),
            relationDelta = RelationDelta(affection = 1, trust = 1),
            cooldownMs = 1300L
        ),
        Live2DReactionConfig(
            hitArea = "body",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapBody",
            expression = "thinking",
            localTextCandidates = listOf(
                "What is it? I am right here.",
                "You got my attention."
            ),
            relationDelta = RelationDelta(mood = 1),
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
