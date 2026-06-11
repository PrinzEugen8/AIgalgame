package com.aigalgame.demo

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.io.IOException
import kotlin.math.abs
import kotlin.math.max

data class Live2DRenderState(
    val character: String = Live2DCharacterConfigs.DefaultCharacter,
    val rendererMode: CharacterRendererMode = CharacterRendererMode.Live2D,
    val staticFallbackCharacter: String = "atri",
    val modelAssetPath: String = "",
    val modelAssetPresent: Boolean = false,
    val rendererAvailable: Boolean = false,
    val expression: String = "calm",
    val motion: String = "idle",
    val nowSpeaking: Boolean = false,
    val mouthOpen: Float = 0f,
    val lookX: Float = 0f,
    val lookY: Float = 0f,
    val lastHitArea: String = "",
    val commandNonce: Long = 0L,
    val statusMessage: String = ""
) {
    val canRenderLive2D: Boolean
        get() = rendererMode == CharacterRendererMode.Live2D && modelAssetPresent && rendererAvailable

    fun toCommand(placement: OutfitPlacement, interactive: Boolean): Live2DRenderCommand {
        return Live2DRenderCommand(
            characterId = character,
            emotion = expression,
            motion = motion,
            mouthOpen = mouthOpen,
            speaking = nowSpeaking,
            lookX = lookX,
            lookY = lookY,
            placement = placement,
            interactive = interactive,
            commandNonce = commandNonce
        )
    }
}

class Live2DController(context: Context, initialCharacter: String = Live2DCharacterConfigs.DefaultCharacter) {
    private val appContext = context.applicationContext
    private var config: Live2DCharacterConfig = Live2DCharacterConfigs.forCharacter(Live2DCharacterConfigs.DefaultCharacter)
    private val cooldownUntilByHitArea = mutableMapOf<String, Long>()
    private var commandNonce = 0L
    private var lookResetAtMs = 0L

    var state by mutableStateOf(Live2DRenderState())
        private set

    init {
        load(initialCharacter)
    }

    fun load(character: String) {
        config = Live2DCharacterConfigs.forCharacter(character)
        val live2DMode = config.rendererMode == CharacterRendererMode.Live2D
        val present = live2DMode && appContext.assetExists(config.modelAssetPath)
        val missingRuntimeAssets = if (live2DMode) {
            Live2DCharacterConfigs.RequiredRuntimeAssetPaths.filterNot { appContext.assetExists(it) }
        } else {
            emptyList()
        }
        val runtimeAvailable = missingRuntimeAssets.isEmpty()
        val rendererAvailable = live2DMode && present && runtimeAvailable
        val status = when {
            !live2DMode -> "Static PNG character mode"
            present && rendererAvailable -> "Official Live2D renderer assets ready"
            !runtimeAvailable -> "Official Live2D assets missing (${missingRuntimeAssets.joinToString()}), using PNG fallback"
            else -> "NEKO model asset missing, using PNG fallback"
        }
        state = state.copy(
            character = config.character,
            rendererMode = config.rendererMode,
            staticFallbackCharacter = config.staticFallbackCharacter,
            modelAssetPath = config.modelAssetPath,
            modelAssetPresent = present,
            rendererAvailable = rendererAvailable,
            statusMessage = status
        )
    }

    fun applyLine(line: DialogueLine?) {
        if (line == null) {
            applyEmotionPose("calm", "idle", explicitMotion = "", explicitExpression = "")
            return
        }
        applyEmotionPose(
            emotion = line.emotion,
            pose = line.pose,
            explicitMotion = line.motion,
            explicitExpression = line.expression
        )
    }

    fun applyPreview(emotion: String, pose: String) {
        applyEmotionPose(emotion, pose, explicitMotion = "", explicitExpression = "")
    }

    fun updateSpeech(speechState: Live2DSpeechState) {
        state = state.copy(
            nowSpeaking = speechState.active,
            mouthOpen = speechState.mouthOpen.coerceIn(0f, 1f)
        )
    }

    fun handleTap(
        normalizedX: Float,
        normalizedY: Float,
        placement: OutfitPlacement,
        relation: RelationState,
        nowMs: Long
    ): Live2DReaction? {
        val (modelX, modelY) = Live2DHitTest.mapScreenToModelSpace(normalizedX, normalizedY, placement)
        val hitArea = config.hitAreas.firstOrNull { it.contains(modelX, modelY) } ?: return null
        val cooldownUntil = cooldownUntilByHitArea[hitArea.id] ?: 0L
        if (nowMs < cooldownUntil) return null

        val reactionConfig = chooseReaction(hitArea.id, relation, nowMs) ?: return null
        cooldownUntilByHitArea[hitArea.id] = nowMs + max(0L, reactionConfig.cooldownMs)
        val tapMotion = chooseTapMotion(hitArea.id, nowMs)
        val reaction = Live2DReaction(
            hitArea = hitArea.id,
            intensity = reactionConfig.intensity,
            motion = tapMotion.ifBlank { reactionConfig.motion },
            expression = reactionConfig.expression,
            text = "",
            relationDelta = RelationDelta()
        )
        lookResetAtMs = nowMs + 2500L
        state = state.copy(
            motion = reaction.motion.ifBlank { state.motion },
            expression = reaction.expression.ifBlank { state.expression },
            lookX = ((modelX - 0.5f) * 2f).coerceIn(-1f, 1f),
            lookY = ((0.5f - modelY) * 2f).coerceIn(-1f, 1f),
            lastHitArea = hitArea.id,
            commandNonce = nextCommandNonce(nowMs)
        )
        return reaction
    }

    fun tickLookDecay(nowMs: Long) {
        if (state.lookX == 0f && state.lookY == 0f) return
        if (nowMs < lookResetAtMs) return

        val decayedX = state.lookX * 0.9f
        val decayedY = state.lookY * 0.9f
        if (abs(decayedX) < 0.02f && abs(decayedY) < 0.02f) {
            state = state.copy(lookX = 0f, lookY = 0f, commandNonce = nextCommandNonce(nowMs))
        } else {
            state = state.copy(lookX = decayedX, lookY = decayedY, commandNonce = nextCommandNonce(nowMs))
        }
    }

    private fun applyEmotionPose(
        emotion: String,
        pose: String,
        explicitMotion: String,
        explicitExpression: String
    ) {
        val emotionBinding = config.emotionBindings[emotion] ?: config.emotionBindings["calm"]
        val mappedPoseMotion = config.poseMotionMap[pose].orEmpty()
        val nextExpression = explicitExpression.ifBlank { emotionBinding?.expression.orEmpty().ifBlank { "calm" } }
        val nextMotion = explicitMotion.ifBlank {
            mappedPoseMotion.ifBlank { emotionBinding?.motion.orEmpty().ifBlank { "idle" } }
        }
        state = state.copy(
            expression = nextExpression,
            motion = nextMotion,
            commandNonce = nextCommandNonce()
        )
    }

    private fun nextCommandNonce(preferred: Long = 0L): Long {
        commandNonce = max(commandNonce + 1L, preferred)
        return commandNonce
    }

    private fun chooseReaction(hitArea: String, relation: RelationState, nowMs: Long): Live2DReactionConfig? {
        val candidates = config.reactions.filter { it.hitArea == hitArea }
        if (candidates.isEmpty()) return null
        if (hitArea == "chest") {
            val desired = if (relation.affection >= 60 && relation.trust >= 35) {
                Live2DReactionIntensity.Flirty
            } else {
                Live2DReactionIntensity.Boundary
            }
            candidates.firstOrNull { it.intensity == desired }?.let { return it }
        }
        return candidates[(nowMs % candidates.size).toInt()]
    }

    private fun chooseTapMotion(hitArea: String, nowMs: Long): String {
        val candidates = config.modelConfig.tapMotions.filter { it.hitArea == hitArea }
        if (candidates.isEmpty()) return ""
        val totalWeight = candidates.sumOf { it.weight.toDouble() }.toFloat().coerceAtLeast(0.01f)
        val pick = (nowMs % 1000L) / 1000f * totalWeight
        var cursor = 0f
        for (candidate in candidates) {
            cursor += candidate.weight
            if (pick <= cursor) {
                return candidate.motion
            }
        }
        return candidates.last().motion
    }

}

private fun Context.assetExists(path: String): Boolean {
    if (path.isBlank()) return false
    return try {
        assets.open(path).use { true }
    } catch (_: IOException) {
        false
    }
}
