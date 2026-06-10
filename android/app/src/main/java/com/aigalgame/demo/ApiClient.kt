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
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.time.OffsetDateTime
import java.util.concurrent.TimeUnit
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

class ApiClient(private val baseUrl: String) {
    private val client = backendHttpClient()

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
        val url = absoluteUrl(path)
        try {
            val builder = Request.Builder().url(url)
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
        } catch (e: IllegalArgumentException) {
            throw IOException("后端地址格式无效，请填写内网穿透提供的完整 https 地址，例如 https://your-domain.example。当前地址：$url", e)
        } catch (e: IOException) {
            throw IOException(describeNetworkFailure(url, e), e)
        }
    }
}

internal fun backendHttpClient(): OkHttpClient {
    return OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .apply {
            if (BuildConfig.DEBUG) {
                trustAllHttpsCertificatesForDebug()
            }
        }
        .build()
}

private fun OkHttpClient.Builder.trustAllHttpsCertificatesForDebug(): OkHttpClient.Builder {
    val trustAllManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }
    val sslContext = SSLContext.getInstance("TLS")
    sslContext.init(null, arrayOf<TrustManager>(trustAllManager), SecureRandom())
    sslSocketFactory(sslContext.socketFactory, trustAllManager)
    hostnameVerifier(HostnameVerifier { _, _ -> true })
    return this
}

fun normalizeBackendUrl(value: String): String {
    val cleaned = value.trim().trimEnd('/')
    if (cleaned.isBlank()) return ""
    if (cleaned.startsWith("https://", ignoreCase = true)) {
        return cleaned
    }
    if (cleaned.startsWith("http://", ignoreCase = true)) {
        return "https://" + cleaned.substringAfter("://")
    }
    return "https://$cleaned"
}

fun describeNetworkFailure(url: String, error: IOException): String {
    val message = error.message.orEmpty()
    val causeParts = mutableListOf<String>()
    var current: Throwable? = error
    repeat(8) {
        val item = current ?: return@repeat
        causeParts += "${item.javaClass.simpleName}: ${item.message.orEmpty()}"
        current = item.cause
    }
    val causeText = causeParts.joinToString(" ")
    return when {
        error is SSLHandshakeException ||
            error is SSLPeerUnverifiedException ||
            causeText.contains("CertPathValidatorException", ignoreCase = true) ||
            causeText.contains("Trust anchor", ignoreCase = true) ->
            "HTTPS 证书不被 Android 信任。请使用带公网可信证书的内网穿透域名；如果是自签或私有 CA，请先把 CA 证书安装到手机，debug 版 App 会信任用户 CA。当前地址：$url"

        error is ConnectException ||
            message.contains("failed to connect", ignoreCase = true) ||
            message.contains("Connection refused", ignoreCase = true) ->
            "连接被拒绝：内网穿透的本地目标端口没有服务在监听。电脑后端当前是 8899，穿透本地目标应填 127.0.0.1:8899；外部访问仍然使用穿透提供的 https 地址。当前地址：$url"

        error is UnknownHostException ->
            "找不到这个后端域名或地址，请检查内网穿透域名是否已启动、手机网络是否可访问。当前地址：$url"

        error is SocketTimeoutException ->
            "连接超时：请确认电脑后端正在运行、内网穿透在线，并且手机能访问该地址。当前地址：$url"

        else -> message.ifBlank { error.javaClass.simpleName }
    }
}

object HttpDownloader {
    private val client = backendHttpClient()

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
