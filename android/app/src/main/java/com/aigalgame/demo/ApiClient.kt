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
    suspend fun calendar(): JSONObject = get("/api/calendar")
    suspend fun journal(): JSONObject = get("/api/journal")
    suspend fun widgetState(): JSONObject = get("/api/widget/state")

    suspend fun postEvent(type: String, text: String = "", replyId: String = "", storyIndex: Int = 0): JSONObject {
        val payload = JSONObject()
        if (text.isNotBlank()) payload.put("text", text)
        if (replyId.isNotBlank()) payload.put("reply_id", replyId)
        if (storyIndex > 0) payload.put("story_index", storyIndex)
        val body = JSONObject()
            .put("event_type", type)
            .put("session_id", "android")
            .put("payload", payload)
            .put("client_context", JSONObject().put("app_state", "foreground"))
        return post("/api/events", body)
    }

    suspend fun likeMoment(momentId: String): JSONObject = post("/api/moments/$momentId/like", JSONObject())

    suspend fun commentMoment(momentId: String, content: String): JSONObject {
        return post("/api/moments/$momentId/comments", JSONObject().put("content", content))
    }

    private suspend fun get(path: String): JSONObject = request("GET", path, null)

    private suspend fun post(path: String, json: JSONObject): JSONObject = request("POST", path, json)

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

fun JSONObject.optObject(name: String): JSONObject = optJSONObject(name) ?: JSONObject()
fun JSONObject.optArray(name: String): JSONArray = optJSONArray(name) ?: JSONArray()
