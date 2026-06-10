package com.aigalgame.demo

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.time.OffsetDateTime
import java.util.concurrent.TimeUnit

class ApiClient(private val baseUrl: String) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    fun absoluteUrl(path: String): String {
        if (path.startsWith("http://") || path.startsWith("https://")) return path
        return baseUrl.trimEnd('/') + path
    }

    suspend fun health(): JSONObject = get("/api/health")
    suspend fun bootstrap(): JSONObject = get("/api/bootstrap")
    suspend fun homeState(): JSONObject = get("/api/state/home")
    suspend fun moments(): JSONObject = get("/api/moments")
    suspend fun calendar(month: String = ""): JSONObject {
        return if (month.isBlank()) get("/api/calendar") else get("/api/calendar?month=$month")
    }
    suspend fun journal(): JSONObject = get("/api/journal")
    suspend fun widgetState(): JSONObject = get("/api/widget/state")
    suspend fun proactivePending(): JSONObject = get("/api/proactive/pending?local_time=${encodedLocalTime()}")
    suspend fun updateLocation(latitude: Double, longitude: Double, accuracyM: Float, provider: String): JSONObject {
        val body = JSONObject()
            .put("latitude", latitude)
            .put("longitude", longitude)
            .put("accuracy_m", accuracyM)
            .put("provider", provider)
            .put("captured_at", OffsetDateTime.now().toString())
            .put("local_time", OffsetDateTime.now().toString())
        return post("/api/location", body)
    }

    suspend fun openingReady(proactiveEventId: String = ""): JSONObject {
        val suffix = if (proactiveEventId.isBlank()) "" else "&proactive_event_id=${encode(proactiveEventId)}"
        return get("/api/opening/ready?session_id=android&local_time=${encodedLocalTime()}$suffix")
    }

    suspend fun postEvent(type: String, text: String = "", replyId: String = "", storyIndex: Int = 0, proactiveEventId: String = ""): JSONObject {
        val payload = JSONObject()
        if (text.isNotBlank()) payload.put("text", text)
        if (replyId.isNotBlank()) payload.put("reply_id", replyId)
        if (storyIndex > 0) payload.put("story_index", storyIndex)
        if (proactiveEventId.isNotBlank()) payload.put("proactive_event_id", proactiveEventId)
        val body = JSONObject()
            .put("event_type", type)
            .put("session_id", "android")
            .put("payload", payload)
            .put("client_context", JSONObject().put("app_state", "foreground").put("local_time", OffsetDateTime.now().toString()))
        return post("/api/events", body)
    }

    suspend fun markProactiveDelivered(eventId: String): JSONObject = post("/api/proactive/$eventId/delivered", JSONObject())
    suspend fun consumeProactive(eventId: String): JSONObject = post("/api/proactive/$eventId/consume", JSONObject())
    suspend fun prepareOpening(proactiveEventId: String = ""): JSONObject {
        val body = JSONObject()
            .put("local_time", OffsetDateTime.now().toString())
            .put("allow_llm", proactiveEventId.isNotBlank())
        if (proactiveEventId.isNotBlank()) body.put("proactive_event_id", proactiveEventId)
        return post("/api/opening/prepare", body)
    }

    suspend fun likeMoment(momentId: String): JSONObject = post("/api/moments/$momentId/like", JSONObject())

    suspend fun commentMoment(momentId: String, content: String): JSONObject {
        return post("/api/moments/$momentId/comments", JSONObject().put("content", content))
    }

    private suspend fun get(path: String): JSONObject = request("GET", path, null)

    private suspend fun post(path: String, json: JSONObject): JSONObject = request("POST", path, json)

    private fun encodedLocalTime(): String {
        return encode(OffsetDateTime.now().toString())
    }

    private fun encode(value: String): String {
        return URLEncoder.encode(value, StandardCharsets.UTF_8.name())
    }

    private suspend fun request(method: String, path: String, json: JSONObject?): JSONObject = withContext(Dispatchers.IO) {
        val builder = Request.Builder().url(absoluteUrl(path))
        if (json != null) {
            builder.method(method, json.toString().toRequestBody("application/json".toMediaType()))
        } else {
            builder.method(method, null)
        }
        client.newCall(builder.build()).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException("HTTP ${response.code}: $text")
            }
            if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }
}

object HttpDownloader {
    private val client = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun bytes(url: String): ByteArray = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("HTTP ${response.code}")
            }
            response.body?.bytes() ?: ByteArray(0)
        }
    }
}

fun JSONObject.optObject(name: String): JSONObject = optJSONObject(name) ?: JSONObject()
fun JSONObject.optArray(name: String): JSONArray = optJSONArray(name) ?: JSONArray()
