package com.aigalgame.demo

import org.json.JSONObject

data class DialogueLine(
    val id: String,
    val text: String,
    val emotion: String = "calm",
    val pose: String = "idle",
    val motion: String = "",
    val expression: String = "",
    val ttsUrl: String = "",
    val ttsError: String = ""
)

data class ReplyOption(
    val id: String,
    val text: String,
    val type: String = "normal",
    val affection: Int = 0,
    val trust: Int = 0,
    val dependency: Int = 0,
    val mood: Int = 0
)

data class RelationState(
    val affection: Int = 85,
    val trust: Int = 60,
    val dependency: Int = 35,
    val mood: Int = 12,
    val stage: String = "初识"
)

data class MomentItem(
    val id: String,
    val authorName: String,
    val text: String,
    val mediaUrl: String,
    val likes: Int,
    val likeActors: List<String> = emptyList(),
    val comments: List<MomentComment>,
    val createdAt: String
)

data class MomentComment(
    val actorName: String,
    val content: String
)

data class MemoryItem(
    val id: String,
    val layer: String,
    val content: String,
    val confidence: Double,
    val importance: Double = 0.0,
    val createdAt: String = ""
)

data class OutfitPlacement(
    val scale: Float = 1.12f,
    val offsetX: Float = 0f,
    val offsetY: Float = 0f,
    val bottomInset: Float = 48f
)

enum class CharacterRendererMode {
    Live2D,
    StaticPng
}

enum class Live2DReactionIntensity(val wireName: String) {
    Soft("soft"),
    Flirty("flirty"),
    Boundary("boundary")
}

data class RelationDelta(
    val affection: Int = 0,
    val trust: Int = 0,
    val dependency: Int = 0,
    val mood: Int = 0
)

data class Live2DReactionConfig(
    val hitArea: String,
    val intensity: Live2DReactionIntensity = Live2DReactionIntensity.Soft,
    val motion: String = "",
    val expression: String = "",
    val localTextCandidates: List<String> = emptyList(),
    val relationDelta: RelationDelta = RelationDelta(),
    val cooldownMs: Long = 1400L
)

data class Live2DTapMotionConfig(
    val hitArea: String,
    val motion: String,
    val weight: Float = 1f
)

data class Live2DModelConfig(
    val name: String,
    val assetPath: String,
    val scale: Float = 1f,
    val initialXShift: Float = 0f,
    val initialYShift: Float = 0f,
    val idleMotionGroupName: String = "Idle",
    val emotionMap: Map<String, String> = emptyMap(),
    val tapMotions: List<Live2DTapMotionConfig> = emptyList()
)

data class Live2DEmotionBinding(
    val expression: String = "",
    val motion: String = ""
)

data class Live2DRenderCommand(
    val characterId: String,
    val emotion: String,
    val motion: String,
    val mouthOpen: Float,
    val speaking: Boolean,
    val lookX: Float,
    val lookY: Float,
    val placement: OutfitPlacement,
    val interactive: Boolean,
    val commandNonce: Long
)

data class Live2DHitArea(
    val id: String,
    val label: String,
    val left: Float,
    val top: Float,
    val right: Float,
    val bottom: Float,
    val priority: Int = 0
) {
    fun contains(x: Float, y: Float): Boolean {
        return x in left..right && y in top..bottom
    }
}

data class Live2DCharacterConfig(
    val character: String,
    val rendererMode: CharacterRendererMode = CharacterRendererMode.StaticPng,
    val staticFallbackCharacter: String = character,
    val modelAssetPath: String,
    val fallbackModelAssetPath: String = "",
    val modelConfig: Live2DModelConfig = Live2DModelConfig(
        name = character,
        assetPath = modelAssetPath
    ),
    val emotionBindings: Map<String, Live2DEmotionBinding>,
    val poseMotionMap: Map<String, String>,
    val hitAreas: List<Live2DHitArea>,
    val reactions: List<Live2DReactionConfig>
)

data class Live2DReaction(
    val hitArea: String,
    val intensity: Live2DReactionIntensity,
    val motion: String,
    val expression: String,
    val text: String,
    val relationDelta: RelationDelta,
    val cooldownMs: Long = 0L,
    val ttsUrl: String = "",
    val ttsDurationMs: Long = 0L
)

data class Live2DSpeechState(
    val active: Boolean = false,
    val mouthOpen: Float = 0f
)

data class TouchCooldownRequest(
    val hitArea: String,
    val cooldownMs: Long,
    val token: Long = System.currentTimeMillis()
)

private const val OutfitPlacementMinScale = 0.25f
private const val OutfitPlacementMaxScale = 4.0f
private const val OutfitPlacementBaseHorizontalRange = 480f
private const val OutfitPlacementUpRange = 900f
private const val OutfitPlacementDownRange = 420f
private const val OutfitPlacementMaxBottomInset = 220f

fun OutfitPlacement.coerceForStage(): OutfitPlacement {
    val nextScale = scale.coerceIn(OutfitPlacementMinScale, OutfitPlacementMaxScale)
    val rangeMultiplier = maxOf(nextScale, 1f)
    return copy(
        scale = nextScale,
        offsetX = offsetX.coerceIn(
            -OutfitPlacementBaseHorizontalRange * rangeMultiplier,
            OutfitPlacementBaseHorizontalRange * rangeMultiplier
        ),
        offsetY = offsetY.coerceIn(
            -OutfitPlacementUpRange * rangeMultiplier,
            OutfitPlacementDownRange * rangeMultiplier
        ),
        bottomInset = bottomInset.coerceIn(0f, OutfitPlacementMaxBottomInset)
    )
}

data class CalendarItem(
    val id: String = "",
    val date: String,
    val startAt: String,
    val title: String,
    val status: String,
    val salience: Int,
    val category: String = "",
    val description: String = "",
    val dayNote: String = ""
)

enum class AppScreen {
    Home,
    DressUp,
    Settings,
    Live2DSelfTest,
    Moments,
    Calendar,
    Journal
}

fun JSONObject.parseLive2dHitAreas(): List<Live2DHitArea> {
    val areas = optJSONArray("hit_areas") ?: return emptyList()
    return buildList {
        for (index in 0 until areas.length()) {
            val item = areas.optJSONObject(index) ?: continue
            add(
                Live2DHitArea(
                    id = item.optString("id", item.optString("area_id")),
                    label = item.optString("label"),
                    left = item.optDouble("left").toFloat(),
                    top = item.optDouble("top").toFloat(),
                    right = item.optDouble("right").toFloat(),
                    bottom = item.optDouble("bottom").toFloat(),
                    priority = item.optInt("priority"),
                )
            )
        }
    }
}

fun JSONObject.parseLive2dReactions(): List<Live2DReactionConfig> {
    val areas = optJSONArray("hit_areas") ?: return emptyList()
    return buildList {
        for (index in 0 until areas.length()) {
            val item = areas.optJSONObject(index) ?: continue
            val hitArea = item.optString("id", item.optString("area_id"))
            val reactions = item.optJSONArray("reactions") ?: continue
            for (reactionIndex in 0 until reactions.length()) {
                val reaction = reactions.optJSONObject(reactionIndex) ?: continue
                add(
                    Live2DReactionConfig(
                        hitArea = hitArea,
                        intensity = when (reaction.optString("intensity")) {
                            "flirty" -> Live2DReactionIntensity.Flirty
                            "boundary" -> Live2DReactionIntensity.Boundary
                            else -> Live2DReactionIntensity.Soft
                        },
                        motion = reaction.optString("motion"),
                        expression = reaction.optString("expression"),
                        cooldownMs = reaction.optLong("cooldown_ms", item.optLong("base_cooldown_ms", 1400L)),
                    )
                )
            }
        }
    }
}
