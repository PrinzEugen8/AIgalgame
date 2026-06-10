package com.aigalgame.demo

object Live2DCharacterConfigs {
    const val OfficialCoreAssetPath = "live2d/sdk/live2dcubismcore.min.js"
    const val OfficialStageAssetPath = "live2d-web/official/index.html"
    const val DefaultModelAssetPath = "live2d/models/Haru/Haru.model3.json"

    private val defaultEmotionBindings = mapOf(
        "calm" to Live2DEmotionBinding(expression = "Neutral", motion = "Idle"),
        "happy" to Live2DEmotionBinding(expression = "Happy", motion = "Happy"),
        "thinking" to Live2DEmotionBinding(expression = "Think", motion = "Think"),
        "shy" to Live2DEmotionBinding(expression = "Shy", motion = "Shy"),
        "sad" to Live2DEmotionBinding(expression = "Sad", motion = "Sad"),
        "angry" to Live2DEmotionBinding(expression = "Angry", motion = "Angry"),
        "sleep" to Live2DEmotionBinding(expression = "Sleep", motion = "Idle")
    )

    private val defaultPoseMotionMap = mapOf(
        "idle" to "Idle",
        "happy" to "Happy",
        "thinking" to "Think",
        "shy" to "Shy",
        "sad" to "Sad",
        "angry" to "Angry",
        "sleep" to "Idle"
    )

    private val haruModelConfig = Live2DModelConfig(
        name = "Haru",
        assetPath = DefaultModelAssetPath,
        scale = 1.0f,
        initialXShift = 0f,
        initialYShift = 0f,
        idleMotionGroupName = "Idle",
        emotionMap = mapOf(
            "calm" to "F01",
            "happy" to "F02",
            "thinking" to "F03",
            "shy" to "F04",
            "sad" to "F05",
            "angry" to "F06",
            "sleep" to "F08"
        ),
        tapMotions = listOf(
            Live2DTapMotionConfig(hitArea = "head", motion = "TapHead", weight = 1f),
            Live2DTapMotionConfig(hitArea = "body", motion = "TapBody", weight = 1f),
            Live2DTapMotionConfig(hitArea = "chest", motion = "TapChest", weight = 1f),
            Live2DTapMotionConfig(hitArea = "hand", motion = "TapHand", weight = 1f)
        )
    )

    private val defaultHitAreas = listOf(
        Live2DHitArea("head", "head", left = 0.34f, top = 0.10f, right = 0.66f, bottom = 0.30f),
        Live2DHitArea("chest", "chest", left = 0.37f, top = 0.34f, right = 0.63f, bottom = 0.52f),
        Live2DHitArea("hand", "hand", left = 0.18f, top = 0.42f, right = 0.82f, bottom = 0.70f),
        Live2DHitArea("body", "body", left = 0.30f, top = 0.30f, right = 0.70f, bottom = 0.82f)
    )

    private val atriReactions = listOf(
        Live2DReactionConfig(
            hitArea = "head",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHead",
            expression = "Happy",
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
            expression = "Shy",
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
            expression = "Awkward",
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
            expression = "Happy",
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
            expression = "Curious",
            localTextCandidates = listOf(
                "What is it? I am right here.",
                "You got my attention."
            ),
            relationDelta = RelationDelta(mood = 1),
            cooldownMs = 1300L
        )
    )

    private val murasameReactions = listOf(
        Live2DReactionConfig(
            hitArea = "head",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHead",
            expression = "Happy",
            localTextCandidates = listOf(
                "I will remember you touched my head like that.",
                "Fine, today I will allow you a little closer."
            ),
            relationDelta = RelationDelta(affection = 1, mood = 1),
            cooldownMs = 1200L
        ),
        Live2DReactionConfig(
            hitArea = "chest",
            intensity = Live2DReactionIntensity.Flirty,
            motion = "TapChest",
            expression = "Shy",
            localTextCandidates = listOf(
                "You really know where to test my reaction.",
                "I cannot pretend I did not notice that.",
                "Do that again and I may misunderstand you on purpose."
            ),
            relationDelta = RelationDelta(affection = 1, dependency = 1, mood = 1),
            cooldownMs = 2200L
        ),
        Live2DReactionConfig(
            hitArea = "chest",
            intensity = Live2DReactionIntensity.Boundary,
            motion = "StepBack",
            expression = "Angry",
            localTextCandidates = listOf(
                "Too fast. Look me in the eyes first.",
                "That kind of thing needs the right mood."
            ),
            relationDelta = RelationDelta(trust = -1, mood = -1),
            cooldownMs = 2600L
        ),
        Live2DReactionConfig(
            hitArea = "hand",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapHand",
            expression = "Happy",
            localTextCandidates = listOf(
                "I will give you my hand, just for a while.",
                "If you hold it, do not let go too quickly."
            ),
            relationDelta = RelationDelta(affection = 1, trust = 1),
            cooldownMs = 1300L
        ),
        Live2DReactionConfig(
            hitArea = "body",
            intensity = Live2DReactionIntensity.Soft,
            motion = "TapBody",
            expression = "Curious",
            localTextCandidates = listOf(
                "Need something from me?",
                "I am listening."
            ),
            relationDelta = RelationDelta(mood = 1),
            cooldownMs = 1300L
        )
    )

    private val configs = mapOf(
        "atri" to Live2DCharacterConfig(
            character = "atri",
            modelAssetPath = "live2d/atri/atri.model3.json",
            fallbackModelAssetPath = DefaultModelAssetPath,
            modelConfig = haruModelConfig,
            emotionBindings = defaultEmotionBindings,
            poseMotionMap = defaultPoseMotionMap,
            hitAreas = defaultHitAreas,
            reactions = atriReactions
        ),
        "murasame" to Live2DCharacterConfig(
            character = "murasame",
            modelAssetPath = "live2d/murasame/murasame.model3.json",
            fallbackModelAssetPath = DefaultModelAssetPath,
            modelConfig = haruModelConfig,
            emotionBindings = defaultEmotionBindings,
            poseMotionMap = defaultPoseMotionMap,
            hitAreas = defaultHitAreas,
            reactions = murasameReactions
        )
    )

    fun forCharacter(character: String): Live2DCharacterConfig {
        return configs[character] ?: configs.getValue("atri")
    }
}
