package com.aigalgame.demo

data class DialogueLine(
    val id: String,
    val text: String,
    val emotion: String = "calm",
    val pose: String = "idle",
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

data class CalendarItem(
    val date: String,
    val startAt: String,
    val title: String,
    val status: String,
    val salience: Int
)

enum class AppScreen {
    Home,
    DressUp,
    Settings,
    Moments,
    Calendar,
    Journal
}
