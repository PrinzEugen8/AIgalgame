package com.aigalgame.demo

import android.content.Context
import android.provider.Settings
import android.util.Log
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import java.time.OffsetDateTime

private const val SAKURA_FCM_TAG = "SakuraFCM"

internal fun writeFcmDebug(context: Context, message: String) {
    if (!BuildConfig.DEBUG) return
    try {
        context.openFileOutput("fcm_debug.txt", Context.MODE_APPEND).use { output ->
            output.write("${OffsetDateTime.now()} $message\n".toByteArray(Charsets.UTF_8))
        }
    } catch (_: Exception) {
    }
}

internal fun androidDeviceId(context: Context): String {
    return Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID) ?: "android"
}

internal fun registerPushToken(context: Context, token: String) {
    if (token.isBlank()) return
    runBlocking {
        val prefs = context.settingsDataStore.data.first()
        val baseUrl = prefs[stringPreferencesKey("server_address")].orEmpty()
        val notificationsEnabled = prefs[booleanPreferencesKey("notifications_enabled")] ?: true
        if (baseUrl.isBlank() || !notificationsEnabled) {
            Log.i(SAKURA_FCM_TAG, "Skip push token registration: baseUrlBlank=${baseUrl.isBlank()} notificationsEnabled=$notificationsEnabled")
            writeFcmDebug(context, "skip register baseUrlBlank=${baseUrl.isBlank()} notificationsEnabled=$notificationsEnabled")
            return@runBlocking
        }
        try {
            ApiClient(baseUrl).registerDevice(
                deviceId = androidDeviceId(context),
                pushToken = token,
                notificationsEnabled = notificationsEnabled
            )
            Log.i(SAKURA_FCM_TAG, "Registered push token with backend")
            writeFcmDebug(context, "registered token backend=$baseUrl tokenLength=${token.length}")
        } catch (exc: Exception) {
            Log.w(SAKURA_FCM_TAG, "Failed to register push token with backend", exc)
            writeFcmDebug(context, "register failed backend=$baseUrl error=${exc.javaClass.simpleName}: ${exc.message}")
        }
    }
}

class SakuraFirebaseMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        Log.i(SAKURA_FCM_TAG, "Received new FCM token")
        registerPushToken(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        val eventId = data["proactive_event_id"].orEmpty()
        if (eventId.isBlank()) return
        val title = data["title"] ?: message.notification?.title ?: "Sakura wants to talk"
        val text = data["text"] ?: message.notification?.body ?: "There is a new message for you."
        val state = JSONObject()
            .put("proactive_event_id", eventId)
            .put("title", title)
            .put("text", text)
            .put("source_type", data["source_type"].orEmpty())
        SakuraWidgetProvider.updateAll(
            applicationContext,
            "Sakura",
            "Has a message",
            text,
            1,
            eventId,
            null
        )
        showProactiveNotification(applicationContext, state)
    }
}
