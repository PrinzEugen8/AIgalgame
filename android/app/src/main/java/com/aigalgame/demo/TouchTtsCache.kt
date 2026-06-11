package com.aigalgame.demo

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

data class CachedTouchLine(
    val hitArea: String,
    val index: Int,
    val lineId: String,
    val contentHash: String,
    val text: String,
    val emotion: String,
    val expression: String,
    val motion: String,
    val ttsUrl: String,
    val localPath: String,
    val ttsDurationMs: Long,
    val cooldownMs: Long,
    var consumed: Boolean = false
)

class TouchTtsCache(private val context: Context) {
    private val rootDir = File(context.filesDir, "touch_tts")
    private var manifest: JSONObject = JSONObject()
    private var cachedTier: String = ""
    private var cachedVoiceId: String = ""
    private var configVersion: String = ""
    private var touchPoolVersion: String = ""

    init {
        rootDir.mkdirs()
        loadManifestFromDisk()
    }

    fun manifestVersion(): String = configVersion
    fun currentTier(): String = cachedTier
    fun touchPoolVersionValue(): String = touchPoolVersion

    data class SyncSummary(
        val areaCount: Int,
        val lineCount: Int,
        val mp3Count: Int,
        val missingAreas: List<String>,
    )

    fun syncSummary(): SyncSummary {
        var lineCount = 0
        var mp3Count = 0
        val missingAreas = mutableListOf<String>()
        val keys = manifest.keys()
        var areaCount = 0
        while (keys.hasNext()) {
            val hitArea = keys.next()
            areaCount += 1
            val lines = manifest.optJSONArray(hitArea) ?: continue
            var areaLines = 0
            var areaMp3 = 0
            for (index in 0 until lines.length()) {
                val item = lines.optJSONObject(index) ?: continue
                areaLines += 1
                val localPath = item.optString("local_path")
                if (localPath.isNotBlank() && File(localPath).exists() && File(localPath).length() > 0L) {
                    areaMp3 += 1
                }
            }
            lineCount += areaLines
            mp3Count += areaMp3
            if (areaLines < 2 || areaMp3 < 1) {
                missingAreas.add(hitArea)
            }
        }
        return SyncSummary(areaCount, lineCount, mp3Count, missingAreas)
    }

    fun shouldRefresh(
        relation: RelationState,
        tier: String,
        voiceProfileId: String,
        live2dConfigVersion: String,
        touchPoolVersionValue: String = "",
    ): Boolean {
        if (configVersion != live2dConfigVersion) return true
        if (touchPoolVersionValue.isNotBlank() && touchPoolVersion != touchPoolVersionValue) return true
        if (cachedTier.isNotBlank() && cachedTier != tier) return true
        if (voiceProfileId.isNotBlank() && cachedVoiceId.isNotBlank() && cachedVoiceId != voiceProfileId) return true
        if (voiceProfileId.isNotBlank() && cachedVoiceId.isBlank()) return true
        return manifest.length() == 0
    }

    suspend fun syncBundle(
        bundle: JSONObject,
        live2dConfigVersion: String,
        resolveUrl: (String) -> String
    ) {
        cachedTier = bundle.optString("tier")
        cachedVoiceId = bundle.optString("voice_profile_id")
        configVersion = live2dConfigVersion
        touchPoolVersion = bundle.optString("touch_pool_version")
        val areas = bundle.optJSONObject("areas") ?: JSONObject()
        val nextManifest = JSONObject()
        val keys = areas.keys()
        while (keys.hasNext()) {
            val hitArea = keys.next()
            val areaPayload = areas.optJSONObject(hitArea) ?: continue
            val lines = areaPayload.optJSONArray("lines") ?: JSONArray()
            val cachedLines = JSONArray()
            for (index in 0 until lines.length()) {
                val line = lines.optJSONObject(index) ?: continue
                val contentHash = line.optString("content_hash").ifBlank {
                    stableHash(line.optString("text"), line.optString("emotion"))
                }
                val remoteUrl = line.optString("tts_audio_url")
                val localFile = File(rootDir, "$contentHash.mp3")
                if (remoteUrl.isNotBlank() && (!localFile.exists() || localFile.length() == 0L)) {
                    runCatching {
                        val bytes = HttpDownloader.bytes(resolveUrl(remoteUrl))
                        if (bytes.size >= 128) {
                            localFile.writeBytes(bytes)
                        }
                    }
                }
                if (!localFile.exists() || localFile.length() == 0L) {
                    continue
                }
                cachedLines.put(
                    JSONObject()
                        .put("hit_area", hitArea)
                        .put("index", line.optInt("index", index))
                        .put("line_id", line.optString("line_id"))
                        .put("content_hash", contentHash)
                        .put("text", line.optString("text"))
                        .put("emotion", line.optString("emotion"))
                        .put("expression", line.optString("expression"))
                        .put("motion", line.optString("motion"))
                        .put("tts_audio_url", remoteUrl)
                        .put("local_path", localFile.absolutePath)
                        .put("tts_duration_ms", line.optLong("tts_duration_ms"))
                        .put("cooldown_ms", line.optLong("cooldown_ms"))
                        .put("consumed", line.optBoolean("consumed"))
                )
            }
            nextManifest.put(hitArea, cachedLines)
        }
        manifest = nextManifest
        saveManifestToDisk()
    }

    fun pickLocalLine(hitArea: String): CachedTouchLine? {
        val lines = manifest.optJSONArray(hitArea) ?: return null
        if (lines.length() == 0) return null
        var available = unreadLines(lines)
        if (available.isEmpty()) {
            resetConsumed(hitArea)
            available = unreadLines(lines)
        }
        if (available.isEmpty()) return null
        val picked = available[(System.currentTimeMillis() % available.size).toInt()]
        return cachedTouchLineFromJson(picked)
    }

    private fun unreadLines(lines: JSONArray): List<JSONObject> = buildList {
        for (index in 0 until lines.length()) {
            val item = lines.optJSONObject(index) ?: continue
            if (!item.optBoolean("consumed")) add(item)
        }
    }

    private fun resetConsumed(hitArea: String) {
        val lines = manifest.optJSONArray(hitArea) ?: return
        for (index in 0 until lines.length()) {
            lines.optJSONObject(index)?.put("consumed", false)
        }
        saveManifestToDisk()
    }

    fun markConsumed(hitArea: String, contentHash: String) {
        val lines = manifest.optJSONArray(hitArea) ?: return
        for (index in 0 until lines.length()) {
            val item = lines.optJSONObject(index) ?: continue
            if (item.optString("content_hash") == contentHash) {
                item.put("consumed", true)
            }
        }
        saveManifestToDisk()
    }

    suspend fun upsertLine(hitArea: String, payload: JSONObject, resolveUrl: (String) -> String): String {
        val lines = manifest.optJSONArray(hitArea) ?: JSONArray().also { manifest.put(hitArea, it) }
        val contentHash = payload.optString("content_hash").ifBlank {
            stableHash(payload.optString("text"), payload.optString("emotion"))
        }
        val remoteUrl = payload.optString("tts_audio_url")
        val localFile = File(rootDir, "$contentHash.mp3")
        if (remoteUrl.isNotBlank()) {
            runCatching {
                if (!localFile.exists() || localFile.length() == 0L) {
                    localFile.writeBytes(HttpDownloader.bytes(resolveUrl(remoteUrl)))
                }
            }
        }
        val entry = JSONObject()
            .put("hit_area", hitArea)
            .put("index", lines.length())
            .put("line_id", payload.optString("line_id"))
            .put("content_hash", contentHash)
            .put("text", payload.optString("text"))
            .put("emotion", payload.optString("emotion"))
            .put("expression", payload.optString("expression"))
            .put("motion", payload.optString("motion"))
            .put("tts_audio_url", remoteUrl)
            .put("local_path", localFile.absolutePath)
            .put("tts_duration_ms", payload.optLong("tts_duration_ms"))
            .put("cooldown_ms", payload.optLong("cooldown_ms"))
            .put("consumed", false)
        lines.put(entry)
        saveManifestToDisk()
        return if (localFile.exists()) localFile.absolutePath else ""
    }

    private fun cachedTouchLineFromJson(item: JSONObject): CachedTouchLine {
        val contentHash = item.optString("content_hash")
        val localPath = item.optString("local_path").ifBlank { File(rootDir, "$contentHash.mp3").absolutePath }
        return CachedTouchLine(
            hitArea = item.optString("hit_area"),
            index = item.optInt("index"),
            lineId = item.optString("line_id"),
            contentHash = contentHash,
            text = item.optString("text"),
            emotion = item.optString("emotion"),
            expression = item.optString("expression"),
            motion = item.optString("motion"),
            ttsUrl = item.optString("tts_audio_url"),
            localPath = localPath,
            ttsDurationMs = item.optLong("tts_duration_ms"),
            cooldownMs = item.optLong("cooldown_ms"),
            consumed = item.optBoolean("consumed")
        )
    }

    private fun saveManifestToDisk() {
        val file = File(rootDir, "manifest.json")
        file.writeText(
            JSONObject()
                .put("config_version", configVersion)
                .put("touch_pool_version", touchPoolVersion)
                .put("tier", cachedTier)
                .put("voice_profile_id", cachedVoiceId)
                .put("areas", manifest)
                .toString()
        )
    }

    private fun loadManifestFromDisk() {
        val file = File(rootDir, "manifest.json")
        if (!file.exists()) return
        runCatching {
            val saved = JSONObject(file.readText())
            configVersion = saved.optString("config_version")
            touchPoolVersion = saved.optString("touch_pool_version")
            cachedTier = saved.optString("tier")
            cachedVoiceId = saved.optString("voice_profile_id")
            manifest = saved.optJSONObject("areas") ?: JSONObject()
        }
    }

    private fun stableHash(vararg parts: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        parts.forEach { part ->
            digest.update(part.toByteArray(Charsets.UTF_8))
            digest.update(0)
        }
        return digest.digest().joinToString("") { byte -> "%02x".format(byte) }.take(32)
    }
}
